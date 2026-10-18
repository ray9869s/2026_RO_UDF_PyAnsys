"""3D pillar HF-only BO and cost-aware MFBO.

Importing this module does not import Fluent or launch a solver. Live
evaluation is a callable the caller supplies. ``offline_replay`` reads a
table and refuses a design that is not in it.

The objective is window module-area LMH. Two constraints are CP excess
(``cp_average - 1``) and pressure drop per length. Both limits, the design
box, and the cost model come from the experiment file. A missing value or
a ``TBD`` placeholder is an error.

HF-equivalent cost is cumulative wall seconds divided by
``reference_hf_wall_s``. Wall seconds are mesh plus solver plus extraction.
A missing part of a successful run is imputed from the fidelity's expected
cost and marked ``cost_imputed``. A design failure is charged the recorded
generate, mesh, solver, and extraction time, and never a reference HF.
Infrastructure waits are logged and are not charged.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_adapter import (
    FAILURE_INFRASTRUCTURE,
    REASON_OPENING_GAP_SLIVER,
    STATUS_DIVERGED,
    STATUS_EXECUTION_FAILED,
    STATUS_INVALID,
    STATUS_VALID,
    PillarDesign,
    _result_from_leaves,
    _run_state,
    evaluate,
    mesh_id_for_settings,
    opening_gap_mm,
    opening_is_near_contact,
    record_from_leaves,
)
from ro.mfbo_fidelity import parse_fidelity_level, run_id_for_level
from ro.paths import MESH_ID_RE, RUN_ID_RE
from ro_2d_pilot.optimization.acquisition import (
    constrained_ei,
    forrester_scores,
    stack_xy,
)
from ro_2d_pilot.optimization.experiment import (
    HF_METHOD,
    METHOD_HF,
    METHOD_MF,
    MF_METHOD,
    MODE_LIVE,
    MODE_REPLAY,
)
from ro_2d_pilot.optimization.gp import AutoregressiveModel

D_F_MM = 0.40
LF = "LF"
HF = "HF"
FIDELITIES = (LF, HF)
STOP_NAME = "STOP"
FAMILY = "pillar"
TBD = "TBD"
SCREEN_MEDIAN = "screen_median"
BUDGET_11H = "11h"
TARGET_BUDGET_WALL_H = 11.0

REQUIRED_FIELDS = (
    "experiment_id",
    "method",
    "mode",
    "seed",
    "run_id",
    "d_f_mm",
    "design_box",
    "cp_excess_max",
    "dp_max_pa_per_m",
    "budget_hf_equivalent",
    "max_sequential",
    "reference_hf_wall_s",
    "lf_over_hf_cost_ratio",
    "candidate_grid",
    "initial_hf",
    "initial_lf",
    "fidelity_table",
    "replay_table",
)
OPTIONAL_FIELDS = (
    "cost_proxy_ratio",
    "screen_csv",
    "infrastructure_backoff_s",
    "infrastructure_cap_s",
)
DEFAULT_INFRASTRUCTURE_BACKOFF_S = (300.0, 600.0, 1200.0)
DEFAULT_INFRASTRUCTURE_CAP_S = 7200.0
_SESSION_MARKERS = (
    "timeout while waiting for backend",
    "backend to be ready",
    "geometry worker died",
    "geometry worker timed out",
    "did not become ready",
    "exited before it was ready",
)

_FAILURE = frozenset({STATUS_DIVERGED, STATUS_INVALID, STATUS_EXECUTION_FAILED})
_STATUSES = frozenset({STATUS_VALID, *_FAILURE})
_QOI = ("lmh", "cp_average", "pressure_drop_per_length_pa_per_m")
_WALL = ("mesh_wall_time_s", "solver_wall_time_s", "extraction_wall_time_s")
_HISTORY_FIELDS = (
    "step",
    "sequential",
    "d_p_mm",
    "d_h_mm",
    "d_f_mm",
    "x1",
    "x2",
    "fidelity",
    "status",
    "failure_reason",
    "failure_class",
    "lmh",
    "cp_average",
    "cp_excess",
    "pressure_drop_per_length_pa_per_m",
    "feasible",
    "generate_wall_time_s",
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
    "wall_s",
    "charged_wall_s",
    "reused_existing",
    "cost_imputed",
    "cumulative_wall_s",
    "cumulative_hf_equivalent",
    "n_lf",
    "n_hf",
    "best_hf_feasible_lmh",
)
_EXPERIMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ReplayMiss(KeyError):
    """The offline table has no CFD result for this design and fidelity."""


class InfrastructureUnavailable(RuntimeError):
    """Generate reported infrastructure, or the geometry worker has no session."""


@dataclass(frozen=True)
class PillarPoint:
    d_p_mm: float
    d_h_mm: float

    def key(self) -> tuple[float, float]:
        return _design_key(self.d_p_mm, self.d_h_mm)


@dataclass
class PillarOutcome:
    d_p_mm: float
    d_h_mm: float
    fidelity: str
    status: str
    lmh: float | None = None
    cp_average: float | None = None
    dp_per_l: float | None = None
    mesh_wall_time_s: float | None = None
    solver_wall_time_s: float | None = None
    extraction_wall_time_s: float | None = None
    generate_wall_time_s: float | None = None
    reused_existing: bool = False
    cost_imputed: bool = False
    failure_reason: str | None = None
    failure_class: str | None = None

    @property
    def key(self) -> tuple[float, float, str]:
        return (*_design_key(self.d_p_mm, self.d_h_mm), self.fidelity)

    @property
    def failed(self) -> bool:
        return self.status in _FAILURE

    @property
    def cp_excess(self) -> float | None:
        if self.cp_average is None:
            return None
        return self.cp_average - 1.0

    @property
    def trains_gp(self) -> bool:
        return (
            self.status == STATUS_VALID
            and self.lmh is not None
            and self.cp_average is not None
            and self.dp_per_l is not None
        )

    def measured_wall_s(self) -> float | None:
        parts = (
            self.mesh_wall_time_s,
            self.solver_wall_time_s,
            self.extraction_wall_time_s,
        )
        if any(part is None for part in parts):
            return None
        return float(sum(parts))


@dataclass
class Dataset:
    outcomes: list[PillarOutcome]

    def __init__(self) -> None:
        self.outcomes = []

    def seen(self, d_p_mm: float, d_h_mm: float, fidelity: str) -> bool:
        key = (*_design_key(d_p_mm, d_h_mm), fidelity)
        return any(item.key == key for item in self.outcomes)

    def add(self, outcome: PillarOutcome) -> None:
        if self.seen(outcome.d_p_mm, outcome.d_h_mm, outcome.fidelity):
            raise ValueError(
                "Refusing to repeat "
                f"d_p_mm={outcome.d_p_mm} d_h_mm={outcome.d_h_mm} "
                f"fidelity={outcome.fidelity}."
            )
        self.outcomes.append(outcome)

    def training(self, fidelity: str) -> list[PillarOutcome]:
        return [
            item
            for item in self.outcomes
            if item.fidelity == fidelity and item.trains_gp
        ]

    def best_hf_feasible(self, config: Experiment3D) -> PillarOutcome | None:
        best: PillarOutcome | None = None
        for item in self.training(HF):
            if not _feasible(item, config):
                continue
            if best is None or (item.lmh or -1.0) > (best.lmh or -1.0):
                best = item
        return best

    def best_hf_feasible_lmh(self, config: Experiment3D) -> float | None:
        best = self.best_hf_feasible(config)
        if best is None:
            return None
        return best.lmh


@dataclass
class WallBudget:
    """HF-equivalent is wall seconds divided by the configured HF wall time."""

    reference_hf_wall_s: float
    lf_over_hf_cost_ratio: float
    limit_hf_equivalent: float
    max_sequential: int
    wall_s: float = 0.0
    n_lf: int = 0
    n_hf: int = 0
    n_sequential: int = 0
    n_failures: int = 0
    imputed_costs: int = 0

    @property
    def hf_equivalent(self) -> float:
        return self.wall_s / self.reference_hf_wall_s

    def expected(self, fidelity: str) -> float:
        if fidelity == HF:
            return 1.0
        if fidelity == LF:
            return self.lf_over_hf_cost_ratio
        raise ValueError(f"Unknown fidelity {fidelity!r}.")

    def can_afford(self, fidelity: str) -> bool:
        if self.n_sequential >= self.max_sequential:
            return False
        return self.hf_equivalent + self.expected(fidelity) <= (
            self.limit_hf_equivalent + 1.0e-9
        )

    def charge(
        self,
        fidelity: str,
        *,
        measured_wall_s: float | None,
        sequential: bool,
        failed: bool,
        imputed: bool,
    ) -> float:
        if fidelity == LF:
            self.n_lf += 1
        elif fidelity == HF:
            self.n_hf += 1
        else:
            raise ValueError(f"Unknown fidelity {fidelity!r}.")
        if sequential:
            self.n_sequential += 1
        if failed:
            self.n_failures += 1
        if measured_wall_s is None:
            measured_wall_s = self.expected(fidelity) * self.reference_hf_wall_s
            imputed = True
        if imputed:
            self.imputed_costs += 1
        self.wall_s += float(measured_wall_s)
        return float(measured_wall_s)

    def charge_known(
        self,
        fidelity: str,
        charged_wall_s: float,
        *,
        sequential: bool,
        failed: bool,
        imputed: bool,
    ) -> None:
        if fidelity == LF:
            self.n_lf += 1
        elif fidelity == HF:
            self.n_hf += 1
        else:
            raise ValueError(f"Unknown fidelity {fidelity!r}.")
        if sequential:
            self.n_sequential += 1
        if failed:
            self.n_failures += 1
        if imputed:
            self.imputed_costs += 1
        self.wall_s += float(charged_wall_s)


@dataclass(frozen=True)
class Experiment3D:
    method: str
    mode: str
    experiment_id: str
    seed: int
    run_id: str
    d_f_mm: float
    d_p_min_mm: float
    d_p_max_mm: float
    d_h_min_mm: float
    d_h_max_mm: float
    cp_excess_max: float
    dp_max_pa_per_m: float
    budget_hf_equivalent: float
    max_sequential: int
    reference_hf_wall_s: float
    lf_over_hf_cost_ratio: float
    candidate_grid: int
    initial_hf: tuple[PillarPoint, ...]
    initial_lf: tuple[PillarPoint, ...]
    fidelity_settings: dict[str, dict[str, Any]]
    replay: ReplayTable | None
    problem: dict[str, Any]
    cost_proxy_ratio: float | None = None
    screen_csv: str | None = None
    infrastructure_backoff_s: tuple[float, ...] = DEFAULT_INFRASTRUCTURE_BACKOFF_S
    infrastructure_cap_s: float = DEFAULT_INFRASTRUCTURE_CAP_S


@dataclass(frozen=True)
class ReplayTable:
    rows: dict[tuple[float, float, str], PillarOutcome]

    def __call__(self, design: PillarPoint, fidelity: str) -> PillarOutcome:
        return self.lookup(design.d_p_mm, design.d_h_mm, fidelity)

    def lookup(self, d_p_mm: float, d_h_mm: float, fidelity: str) -> PillarOutcome:
        key = (*_design_key(d_p_mm, d_h_mm), fidelity)
        found = self.rows.get(key)
        if found is None:
            raise ReplayMiss(
                "offline_replay has no CFD result for "
                f"d_p_mm={d_p_mm} d_h_mm={d_h_mm} fidelity={fidelity}."
            )
        found.reused_existing = True
        return found

    def designs(self) -> list[PillarPoint]:
        seen: list[PillarPoint] = []
        keys: set[tuple[float, float]] = set()
        for d_p, d_h, _fidelity in self.rows:
            if (d_p, d_h) in keys:
                continue
            keys.add((d_p, d_h))
            seen.append(PillarPoint(d_p, d_h))
        seen.sort(key=lambda item: item.key())
        return seen


class LiveEvaluator:
    """One adapter evaluation. Does not import the real drivers itself."""

    def __init__(
        self,
        data_root: Path,
        fidelity_settings: Mapping[str, Mapping[str, Any]],
        run_id: str,
        d_f_mm: float,
        drivers: Any,
    ) -> None:
        self.data_root = data_root
        self.fidelity_settings = dict(fidelity_settings)
        self.run_id = run_id
        self.d_f_mm = d_f_mm
        self.drivers = drivers

    def __call__(self, design: PillarPoint, fidelity: str) -> PillarOutcome:
        point = PillarDesign(design.d_p_mm, design.d_h_mm, self.d_f_mm)
        settings = parse_fidelity_level(self.fidelity_settings[fidelity])
        n_active = settings["n_active"] if settings.get("extended") else None
        geo_id = format_mfbo_pillar_geo_id(
            point.d_p_mm,
            point.d_h_mm,
            point.d_f_mm,
            n_active=n_active,
        )
        mesh_id = mesh_id_for_settings(settings)
        checked_run = run_id_for_level(self.run_id, settings)
        run_dir = self.data_root / "runs" / FAMILY / geo_id / mesh_id / checked_run
        reused = _run_state(run_dir) == "success"
        try:
            record = evaluate(
                point,
                fidelity,
                run_id=self.run_id,
                data_root=self.data_root,
                fidelity_table=self.fidelity_settings,
                drivers=self.drivers,
            )
        except InfrastructureUnavailable:
            raise
        except Exception as exc:
            if _session_infrastructure(exc):
                raise InfrastructureUnavailable(str(exc)) from exc
            return PillarOutcome(
                d_p_mm=design.d_p_mm,
                d_h_mm=design.d_h_mm,
                fidelity=fidelity,
                status=STATUS_EXECUTION_FAILED,
                reused_existing=False,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
        if _generate_infrastructure(record):
            detail = record.get("failure_reason") or FAILURE_INFRASTRUCTURE
            raise InfrastructureUnavailable(str(detail))
        return outcome_from_record(record, reused_existing=reused)


def campaign_pillar_diameter_mm(geo_id: str) -> tuple[float, float]:
    """``P_p60_h15`` -> ``(0.60, 0.15)``. Tokens are hundredths of a millimetre."""
    parts = geo_id.split("_")
    if len(parts) != 3 or parts[0] != "P" or not parts[1].startswith("p") or not parts[2].startswith("h"):
        raise ValueError(f"{geo_id!r} is not a campaign pillar geo_id.")
    return int(parts[1][1:]) / 100.0, int(parts[2][1:]) / 100.0


def campaign_pillar_geo_ids() -> tuple[str, ...]:
    return tuple(geo_id for geo_id in CAMPAIGN_GEO_ID_ORDER if geo_id.startswith("P_"))


def build_campaign_replay_table(
    data_root: str | Path,
    *,
    run_id: str,
    mesh_id: str,
    fidelity: str,
) -> dict[str, Any]:
    """Read campaign pillar leaves at one mesh and return an offline table.

    The nine campaign ids are ``P_p60/80/100`` by ``h00/15/30``, which are
    ``d_p_mm`` 0.60/0.80/1.00 and ``d_h_mm`` 0/0.15/0.30. ``d_f_mm`` is 0.40.
    A geo_id with no run leaf is omitted. A leaf that exists is recorded,
    including a failure. The caller refuses a design that was omitted.
    """
    root = Path(data_root)
    if not root.is_absolute():
        raise ValueError(f"data root must be absolute, got {data_root!r}.")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")
    if not isinstance(mesh_id, str) or MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"Invalid mesh_id: {mesh_id!r}.")
    if fidelity not in FIDELITIES:
        raise ValueError(f"fidelity must be one of {FIDELITIES}, got {fidelity!r}.")
    rows = []
    absent = []
    for geo_id in campaign_pillar_geo_ids():
        d_p_mm, d_h_mm = campaign_pillar_diameter_mm(geo_id)
        run_dir = root / "runs" / FAMILY / geo_id / mesh_id / run_id
        if not run_dir.exists():
            absent.append(geo_id)
            continue
        mesh_dir = root / "meshes" / FAMILY / geo_id / mesh_id
        record = _result_from_leaves(
            {
                "geo_id": geo_id,
                "mesh_id": mesh_id,
                "run_id": run_id,
                "fidelity": fidelity,
                "d_p_mm": d_p_mm,
                "d_h_mm": d_h_mm,
                "d_f_mm": D_F_MM,
            },
            mesh_dir,
            run_dir,
            run_dir / "post" / "reports" / "summary_metrics_wide.csv",
        )
        rows.append(_replay_row(record))
    if not rows:
        raise ValueError(
            "No campaign pillar run leaves at "
            f"{root} mesh_id={mesh_id} run_id={run_id}. Absent: {absent}."
        )
    return {
        "run_id": run_id,
        "mesh_id": mesh_id,
        "fidelity": fidelity,
        "d_f_mm": D_F_MM,
        "absent_geo_ids": absent,
        "rows": rows,
    }


def load_replay_table(path: Path, config_fields: Mapping[str, Any]) -> ReplayTable:
    payload = _read_json_object(path, "replay table")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError(f"replay table {path} needs a non-empty rows list.")
    d_f = _real("d_f_mm", config_fields["d_f_mm"])
    parsed: dict[tuple[float, float, str], PillarOutcome] = {}
    for index, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            raise TypeError(f"replay row {index} must be an object.")
        outcome = _outcome_from_replay_row(row, d_f_mm=d_f)
        if outcome.key in parsed:
            raise ValueError(f"Duplicate replay row {outcome.key}.")
        if not _inside(
            outcome.d_p_mm,
            outcome.d_h_mm,
            config_fields["d_p_min_mm"],
            config_fields["d_p_max_mm"],
            config_fields["d_h_min_mm"],
            config_fields["d_h_max_mm"],
        ):
            raise ValueError(
                f"Replay design d_p_mm={outcome.d_p_mm} d_h_mm={outcome.d_h_mm} "
                "is outside the design box."
            )
        parsed[outcome.key] = outcome
    return ReplayTable(parsed)


def load_experiment(
    path: Path,
    *,
    repo_root: Path,
    data_root: Path,
    method_override: str | None = None,
) -> Experiment3D:
    """Load an experiment file. Missing fields and ``TBD`` limits are errors."""
    payload = _read_json_object(path, "experiment config")
    if method_override is not None:
        if method_override not in {METHOD_HF, METHOD_MF}:
            raise ValueError(f"Unknown method {method_override!r}.")
        payload["method"] = method_override
    unknown = sorted(set(payload) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise ValueError(f"Unknown experiment fields: {unknown}.")
    missing = [key for key in REQUIRED_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"Experiment config is missing {missing}.")
    method = payload["method"]
    mode = payload["mode"]
    if method not in {METHOD_HF, METHOD_MF}:
        raise ValueError(f"Unknown method {method!r}.")
    if mode not in {MODE_REPLAY, MODE_LIVE}:
        raise ValueError(f"Unknown mode {mode!r}.")
    _refuse_limit_placeholders(payload)
    experiment_id = payload["experiment_id"]
    if not isinstance(experiment_id, str) or _EXPERIMENT_ID_RE.fullmatch(experiment_id) is None:
        raise ValueError(f"Invalid experiment_id: {experiment_id!r}.")
    seed = payload["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be an integer >= 0, got {seed!r}.")
    run_id = payload["run_id"]
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")
    d_f_mm = _real("d_f_mm", payload["d_f_mm"])
    if abs(d_f_mm - D_F_MM) > 1.0e-12:
        raise ValueError(f"d_f_mm is fixed at {D_F_MM}, got {payload['d_f_mm']!r}.")
    d_p_min, d_p_max, d_h_min, d_h_max = _design_box(payload["design_box"])
    cp_excess_max = _real("cp_excess_max", payload["cp_excess_max"])
    dp_max = _real("dp_max_pa_per_m", payload["dp_max_pa_per_m"])
    max_sequential = payload["max_sequential"]
    if isinstance(max_sequential, bool) or not isinstance(max_sequential, int) or max_sequential < 0:
        raise ValueError(f"max_sequential must be an integer >= 0, got {max_sequential!r}.")
    reference, screen_csv = _reference_wall(payload, data_root)
    ratio = _positive("lf_over_hf_cost_ratio", payload["lf_over_hf_cost_ratio"])
    budget = _budget_limit(payload["budget_hf_equivalent"], reference)
    proxy = _optional_cost_proxy(payload)
    backoff = _infrastructure_backoff(payload)
    cap_s = _infrastructure_cap(payload)
    grid = payload["candidate_grid"]
    if isinstance(grid, bool) or not isinstance(grid, int) or grid < 2:
        raise ValueError(f"candidate_grid must be an integer >= 2, got {grid!r}.")
    box_fields = {
        "d_p_min_mm": d_p_min,
        "d_p_max_mm": d_p_max,
        "d_h_min_mm": d_h_min,
        "d_h_max_mm": d_h_max,
    }
    initial_hf = _initial_points("initial_hf", payload["initial_hf"], box_fields, allow_empty=False)
    initial_lf = _initial_points(
        "initial_lf",
        payload["initial_lf"],
        box_fields,
        allow_empty=method == METHOD_HF,
    )
    fidelity_path = _resolve_repo_path(payload["fidelity_table"], repo_root, "fidelity_table")
    needed = (HF,) if method == METHOD_HF else FIDELITIES
    settings = _load_fidelity_settings(fidelity_path, needed)
    replay: ReplayTable | None
    replay_token: str | None
    if mode == MODE_REPLAY:
        if not isinstance(payload["replay_table"], str) or not payload["replay_table"].strip():
            raise ValueError("offline_replay requires replay_table to be a path.")
        replay_path = _resolve_data_path(payload["replay_table"], data_root, "replay_table")
        replay = load_replay_table(replay_path, {**box_fields, "d_f_mm": d_f_mm})
        replay_token = payload["replay_table"]
        replay_hash = _sha256_json(_replay_hash_payload(replay))
    else:
        if payload["replay_table"] is not None:
            raise ValueError("live mode requires replay_table to be null.")
        replay = None
        replay_token = None
        replay_hash = None
    problem = {
        "experiment_id": experiment_id,
        "method": method,
        "mode": mode,
        "seed": seed,
        "run_id": run_id,
        "d_f_mm": d_f_mm,
        "design_box": {"d_p_mm": [d_p_min, d_p_max], "d_h_mm": [d_h_min, d_h_max]},
        "cp_excess_max": cp_excess_max,
        "dp_max_pa_per_m": dp_max,
        "reference_hf_wall_s": reference,
        "lf_over_hf_cost_ratio": ratio,
        "candidate_grid": grid,
        "initial_hf": [_point_dict(item) for item in initial_hf],
        "initial_lf": [_point_dict(item) for item in initial_lf],
        "fidelity_table": payload["fidelity_table"],
        "fidelity_sha256": _sha256_json(settings),
        "replay_table": replay_token,
        "replay_sha256": replay_hash,
    }
    if proxy is not None:
        problem["cost_proxy_ratio"] = proxy
    return Experiment3D(
        method=method,
        mode=mode,
        experiment_id=experiment_id,
        seed=seed,
        run_id=run_id,
        d_f_mm=d_f_mm,
        d_p_min_mm=d_p_min,
        d_p_max_mm=d_p_max,
        d_h_min_mm=d_h_min,
        d_h_max_mm=d_h_max,
        cp_excess_max=cp_excess_max,
        dp_max_pa_per_m=dp_max,
        budget_hf_equivalent=budget,
        max_sequential=max_sequential,
        reference_hf_wall_s=reference,
        lf_over_hf_cost_ratio=ratio,
        candidate_grid=grid,
        initial_hf=initial_hf,
        initial_lf=initial_lf,
        fidelity_settings=settings,
        replay=replay,
        problem=problem,
        cost_proxy_ratio=proxy,
        screen_csv=screen_csv,
        infrastructure_backoff_s=backoff,
        infrastructure_cap_s=cap_s,
    )


def experiment_dir(data_root: Path, method: str, experiment_id: str) -> Path:
    return data_root / "studies" / "optimization" / "3d" / method / experiment_id


def require_initial_designs(config: Experiment3D) -> None:
    """Refuse an initial design the opening-gap check would not evaluate."""
    for point in config.initial_hf:
        _require_evaluable(config, point, HF)
    if config.method == METHOD_MF:
        for point in config.initial_lf:
            _require_evaluable(config, point, LF)


def format_plan(config: Experiment3D) -> str:
    """Initial designs and the HF-equivalent budget. Does not launch Fluent."""
    hours = config.budget_hf_equivalent * config.reference_hf_wall_s / 3600.0
    hf_expected = float(len(config.initial_hf))
    mf_expected = hf_expected + len(config.initial_lf) * config.lf_over_hf_cost_ratio
    lines = [
        (
            f"experiment_id={config.experiment_id} method={config.method} "
            f"mode={config.mode} seed={config.seed}"
        ),
        (
            f"box d_p_mm=[{config.d_p_min_mm}, {config.d_p_max_mm}] "
            f"d_h_mm=[{config.d_h_min_mm}, {config.d_h_max_mm}] d_f_mm={config.d_f_mm}"
        ),
        f"run_id={config.run_id} cp_excess_max={config.cp_excess_max} dp_max_pa_per_m={config.dp_max_pa_per_m}",
        (
            f"reference_hf_wall_s={config.reference_hf_wall_s} "
            f"lf_over_hf_cost_ratio={config.lf_over_hf_cost_ratio} "
            f"cost_proxy_ratio={config.cost_proxy_ratio}"
        ),
        (
            f"budget_hf_equivalent={config.budget_hf_equivalent} "
            f"budget_wall_h={hours} max_sequential={config.max_sequential}"
        ),
        (
            "expected initial HF-equivalent if each new run costs the reference: "
            f"hf_bo={hf_expected} mfbo={mf_expected}"
        ),
        "hf_bo initial (recorded wall time is charged; these are not sequential queries):",
    ]
    for point in config.initial_hf:
        lines.append(f"  HF d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm}")
    lines.append("mfbo initial:")
    for point in config.initial_hf:
        lines.append(f"  HF d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm}")
    for point in config.initial_lf:
        lines.append(f"  LF d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm}")
    lines.append(
        "A leaf already on disk is reused and charged its recorded mesh+solver+extraction wall time."
    )
    lines.append(
        "Initial evaluations are charged even when they exceed the budget. "
        "Sequential queries stop when the next expected cost does not fit."
    )
    waits = ", ".join(str(item) for item in config.infrastructure_backoff_s)
    lines.append(
        "Infrastructure failures pause and retry the same design "
        f"(backoff_s={waits}, cap_s={config.infrastructure_cap_s}). "
        "That wait is not charged. A design failure is charged its recorded wall time."
    )
    return "\n".join(lines)


def format_recorded_initial_charges(config: Experiment3D, data_root: Path) -> str:
    """Wall time already on disk for the shared initial designs. No Fluent."""
    lines = ["recorded initial charges:"]
    hf_sum = 0.0
    lf_sum = 0.0
    seen: list[tuple[PillarPoint, str]] = [(point, HF) for point in config.initial_hf]
    seen.extend((point, LF) for point in config.initial_lf)
    for point, fidelity in seen:
        record = record_from_leaves(
            PillarDesign(point.d_p_mm, point.d_h_mm, config.d_f_mm),
            fidelity,
            run_id=config.run_id,
            data_root=data_root,
            fidelity_table=config.fidelity_settings,
        )
        wall = _recorded_wall_s(record)
        status = record.get("status")
        if wall is None or status == STATUS_EXECUTION_FAILED:
            lines.append(
                f"  {fidelity} d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm} "
                f"status={status} recorded=absent"
            )
            continue
        equivalent = wall / config.reference_hf_wall_s
        if fidelity == HF:
            hf_sum += equivalent
        else:
            lf_sum += equivalent
        lines.append(
            f"  {fidelity} d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm} "
            f"status={status} wall_s={wall} hf_equivalent={equivalent}"
        )
    lines.append(f"recorded_hf_equivalent hf_bo={hf_sum} mfbo={hf_sum + lf_sum}")
    return "\n".join(lines)


def _recorded_failure_wall_s(outcome: PillarOutcome) -> float:
    """Sum recorded stage times. A missing stage is zero, not a reference HF."""
    total = 0.0
    for part in (
        outcome.generate_wall_time_s,
        outcome.mesh_wall_time_s,
        outcome.solver_wall_time_s,
        outcome.extraction_wall_time_s,
    ):
        if part is not None:
            total += float(part)
    return total


def _session_infrastructure(exc: BaseException) -> bool:
    if getattr(exc, "failure_class", None) == FAILURE_INFRASTRUCTURE:
        return True
    text = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__
    if cause is not None:
        text = f"{text} {type(cause).__name__}: {cause}"
    folded = text.casefold()
    return any(marker in folded for marker in _SESSION_MARKERS)


def _generate_infrastructure(record: Mapping[str, Any]) -> bool:
    if record.get("failure_class") != FAILURE_INFRASTRUCTURE:
        return False
    leaf = str(record.get("leaf_path") or "").replace("\\", "/")
    return "/geometries/" in f"/{leaf}"


def _infrastructure_event(
    design: PillarPoint,
    fidelity: str,
    exc: BaseException,
    *,
    spent: float,
    wait_s: float,
    action: str,
) -> dict[str, Any]:
    return {
        "d_p_mm": design.d_p_mm,
        "d_h_mm": design.d_h_mm,
        "fidelity": fidelity,
        "action": action,
        "wait_s": wait_s,
        "spent_s": spent,
        "error": str(exc),
    }


def _write_infrastructure(
    destination: Path,
    events: list[dict[str, Any]],
    infrastructure_wall_s: float,
) -> None:
    _write_json(
        destination / "infrastructure.json",
        {"infrastructure_wall_s": infrastructure_wall_s, "events": events},
    )


def _infrastructure_backoff(payload: Mapping[str, Any]) -> tuple[float, ...]:
    if "infrastructure_backoff_s" not in payload:
        return DEFAULT_INFRASTRUCTURE_BACKOFF_S
    value = payload["infrastructure_backoff_s"]
    if not isinstance(value, list) or not value:
        raise ValueError(
            "infrastructure_backoff_s must be a non-empty list of seconds, "
            f"got {value!r}."
        )
    return tuple(_positive("infrastructure_backoff_s", item) for item in value)


def _infrastructure_cap(payload: Mapping[str, Any]) -> float:
    if "infrastructure_cap_s" not in payload:
        return DEFAULT_INFRASTRUCTURE_CAP_S
    return _positive("infrastructure_cap_s", payload["infrastructure_cap_s"])


def _same_backoff(stored: Any, current: tuple[float, ...]) -> bool:
    if not isinstance(stored, list) or len(stored) != len(current):
        return False
    try:
        parsed = tuple(float(item) for item in stored)
    except (TypeError, ValueError):
        return False
    return parsed == current


def _recorded_wall_s(record: Mapping[str, Any]) -> float | None:
    parts = [
        _optional_real(record.get(name))
        for name in ("mesh_wall_time_s", "solver_wall_time_s", "extraction_wall_time_s")
    ]
    if any(part is None for part in parts):
        return None
    return float(sum(parts))


def run_optimization(
    config: Experiment3D,
    evaluator: Callable[[PillarPoint, str], PillarOutcome],
    destination: Path,
    *,
    pause: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    dataset = Dataset()
    budget = WallBudget(
        reference_hf_wall_s=config.reference_hf_wall_s,
        lf_over_hf_cost_ratio=config.lf_over_hf_cost_ratio,
        limit_hf_equivalent=config.budget_hf_equivalent,
        max_sequential=config.max_sequential,
    )
    history = _load_history(destination, config, dataset, budget)
    if _stopped(destination) and not history:
        return {
            "stopped": True,
            "stop_reason": "stop_file",
            "wrote": False,
            "experiment_id": config.experiment_id,
        }
    waiter = time.sleep if pause is None else pause
    stopped = False
    stop_reason: str | None = None
    infrastructure: list[dict[str, Any]] = []
    infrastructure_wall_s = 0.0

    def absorb(outcome: PillarOutcome, *, sequential: bool) -> None:
        _absorb(config, dataset, budget, history, outcome, sequential=sequential)
        _write(
            destination,
            config,
            dataset,
            budget,
            history,
            stopped=False,
            stop_reason=None,
            infrastructure_wall_s=infrastructure_wall_s,
        )

    def attempt(design: PillarPoint, fidelity: str, *, sequential: bool) -> bool:
        nonlocal stopped, stop_reason, infrastructure_wall_s
        schedule = config.infrastructure_backoff_s
        index = 0
        spent = 0.0
        while True:
            if _stopped(destination):
                stopped = True
                stop_reason = "stop_file"
                return False
            try:
                outcome = evaluator(design, fidelity)
            except InfrastructureUnavailable as exc:
                delay = schedule[min(index, len(schedule) - 1)]
                index += 1
                if spent + delay > config.infrastructure_cap_s + 1.0e-9:
                    stop_reason = "infrastructure"
                    infrastructure.append(
                        _infrastructure_event(design, fidelity, exc, spent=spent, wait_s=0.0, action="stop")
                    )
                    _write_infrastructure(destination, infrastructure, infrastructure_wall_s)
                    return False
                waiter(float(delay))
                spent += float(delay)
                infrastructure_wall_s += float(delay)
                infrastructure.append(
                    _infrastructure_event(
                        design, fidelity, exc, spent=spent, wait_s=float(delay), action="retry"
                    )
                )
                _write_infrastructure(destination, infrastructure, infrastructure_wall_s)
                continue
            absorb(outcome, sequential=sequential)
            return True

    for design, fidelity in _initial_plan(config):
        if dataset.seen(design.d_p_mm, design.d_h_mm, fidelity):
            continue
        if _stopped(destination):
            stopped = True
            stop_reason = "stop_file"
            break
        _require_evaluable(config, design, fidelity)
        if not attempt(design, fidelity, sequential=False):
            break
    if stop_reason is None and not stopped:
        while True:
            if _stopped(destination):
                stopped = True
                stop_reason = "stop_file"
                break
            proposal = _propose(config, dataset)
            if proposal is None:
                break
            design, fidelity = proposal
            if not budget.can_afford(fidelity):
                break
            if not attempt(design, fidelity, sequential=True):
                break
    if stop_reason is None and not stopped:
        recommended = _recommend(config, dataset)
        if recommended is not None and not dataset.seen(
            recommended.d_p_mm, recommended.d_h_mm, HF
        ):
            if _stopped(destination):
                stopped = True
                stop_reason = "stop_file"
            else:
                _require_evaluable(config, recommended, HF)
                attempt(recommended, HF, sequential=True)
    if _stopped(destination):
        stopped = True
        stop_reason = stop_reason or "stop_file"
    summary = _write(
        destination,
        config,
        dataset,
        budget,
        history,
        stopped=stopped,
        stop_reason=stop_reason,
        infrastructure_wall_s=infrastructure_wall_s,
    )
    return summary


def geometry_block_reason(
    d_p_mm: float,
    d_h_mm: float,
    d_f_mm: float,
    m_min_mm: float,
) -> str | None:
    """Reason the design is excluded, or None when the opening can be meshed.

    ``|G| < m_min`` is near contact. On the 17-point grid the D_p 0.60
    zero crossing lies between d_h 0.01875 mm (G about +7.3 µm) and
    d_h 0.0375 mm (G about -2.1 µm). m_min is 6 µm, which the surface
    mesher cannot resolve. (0.60, 0.1125) is about -40 µm, a merged
    opening, so this check does not reject it.
    """
    try:
        gap = opening_gap_mm(d_p_mm, d_h_mm, d_f_mm)
    except ValueError as exc:
        return str(exc)
    if opening_is_near_contact(gap, m_min_mm):
        return (
            f"opening gap {gap} mm is within m_min {m_min_mm} mm of contact "
            f"({REASON_OPENING_GAP_SLIVER})."
        )
    return None


def outcome_from_record(record: Mapping[str, Any], *, reused_existing: bool) -> PillarOutcome:
    status = record.get("status")
    if status not in _STATUSES:
        raise ValueError(f"Unexpected evaluation status {status!r}.")
    return PillarOutcome(
        d_p_mm=_real("d_p_mm", record["d_p_mm"]),
        d_h_mm=_real("d_h_mm", record["d_h_mm"]),
        fidelity=str(record["fidelity"]),
        status=str(status),
        lmh=_optional_real(record.get("lmh")),
        cp_average=_optional_real(record.get("cp_average")),
        dp_per_l=_optional_real(record.get("pressure_drop_per_length_pa_per_m")),
        mesh_wall_time_s=_optional_real(record.get("mesh_wall_time_s")),
        solver_wall_time_s=_optional_real(record.get("solver_wall_time_s")),
        extraction_wall_time_s=_optional_real(record.get("extraction_wall_time_s")),
        generate_wall_time_s=_optional_real(record.get("generate_wall_time_s")),
        reused_existing=reused_existing,
        failure_reason=_optional_text(record.get("failure_reason")),
        failure_class=_optional_text(record.get("failure_class")),
    )


def _initial_plan(config: Experiment3D) -> list[tuple[PillarPoint, str]]:
    plan = [(design, HF) for design in config.initial_hf]
    if config.method == METHOD_MF:
        plan.extend((design, LF) for design in config.initial_lf)
    return plan


def _require_evaluable(config: Experiment3D, design: PillarPoint, fidelity: str) -> None:
    reason = geometry_block_reason(
        design.d_p_mm,
        design.d_h_mm,
        config.d_f_mm,
        _m_min(config, fidelity),
    )
    if reason is not None:
        raise ValueError(
            f"Initial design d_p_mm={design.d_p_mm} d_h_mm={design.d_h_mm} "
            f"at {fidelity} is not evaluated: {reason}"
        )


def _propose(config: Experiment3D, dataset: Dataset) -> tuple[PillarPoint, str] | None:
    objective, pressure, excess = _fit(config, dataset)
    incumbent = dataset.best_hf_feasible_lmh(config)
    best: tuple[float, PillarPoint, str] | None = None
    for design in _candidates(config):
        mean_l, var_l, mean_p, var_p, mean_c, var_c, correlation = _predict(
            config, objective, pressure, excess, design
        )
        score = constrained_ei(
            mean_l,
            var_l,
            mean_p,
            var_p,
            incumbent=incumbent,
            limit=config.dp_max_pa_per_m,
            extra_constraints=((mean_c, var_c, config.cp_excess_max),),
        )
        if config.method == METHOD_HF:
            choices = {HF: score}
        else:
            choices = forrester_scores(
                score,
                correlation,
                cost_ratio=config.lf_over_hf_cost_ratio,
                high_name=HF,
                low_name=LF,
            )
        for fidelity, value in choices.items():
            if dataset.seen(design.d_p_mm, design.d_h_mm, fidelity):
                continue
            if geometry_block_reason(
                design.d_p_mm, design.d_h_mm, config.d_f_mm, _m_min(config, fidelity)
            ):
                continue
            if config.mode == MODE_REPLAY and not _replay_has(config, design, fidelity):
                continue
            if best is None or value > best[0]:
                best = (value, design, fidelity)
    if best is None:
        return None
    return best[1], best[2]


def _recommend(config: Experiment3D, dataset: Dataset) -> PillarPoint | None:
    objective, pressure, excess = _fit(config, dataset)
    best_design: PillarPoint | None = None
    best_mean = -1.0e300
    for design in _candidates(config):
        if geometry_block_reason(
            design.d_p_mm, design.d_h_mm, config.d_f_mm, _m_min(config, HF)
        ):
            continue
        if config.mode == MODE_REPLAY and not _replay_has(config, design, HF):
            continue
        mean_l, _, mean_p, _, mean_c, _, _ = _predict(
            config, objective, pressure, excess, design
        )
        if mean_p > config.dp_max_pa_per_m or mean_c > config.cp_excess_max:
            continue
        if mean_l > best_mean:
            best_mean = mean_l
            best_design = design
    if best_design is not None:
        return best_design
    incumbent = dataset.best_hf_feasible(config)
    if incumbent is None:
        return None
    return PillarPoint(incumbent.d_p_mm, incumbent.d_h_mm)


def _fit(
    config: Experiment3D,
    dataset: Dataset,
) -> tuple[AutoregressiveModel, AutoregressiveModel, AutoregressiveModel]:
    low = dataset.training(LF)
    high = dataset.training(HF)

    def fit(values_of) -> AutoregressiveModel:
        return AutoregressiveModel().fit(
            stack_xy([_x(config, item) for item in low]),
            np.asarray([values_of(item) for item in low], dtype=float),
            stack_xy([_x(config, item) for item in high]),
            np.asarray([values_of(item) for item in high], dtype=float),
        )

    return (
        fit(lambda item: item.lmh),
        fit(lambda item: item.dp_per_l),
        fit(lambda item: item.cp_excess),
    )


def _predict(
    config: Experiment3D,
    objective: AutoregressiveModel,
    pressure: AutoregressiveModel,
    excess: AutoregressiveModel,
    design: PillarPoint,
) -> tuple[float, float, float, float, float, float, float]:
    x = np.asarray([_x(config, design)], dtype=float)
    mean_l, var_l = objective.predict_high(x)
    mean_p, var_p = pressure.predict_high(x)
    mean_c, var_c = excess.predict_high(x)
    correlation = float(objective.correlation_with_low(x)[0])
    return (
        float(mean_l[0]),
        float(var_l[0]),
        float(mean_p[0]),
        float(var_p[0]),
        float(mean_c[0]),
        float(var_c[0]),
        correlation,
    )


def _candidates(config: Experiment3D) -> list[PillarPoint]:
    if config.mode == MODE_REPLAY:
        if config.replay is None:
            raise ValueError("offline_replay has no replay table.")
        return config.replay.designs()
    span = config.candidate_grid - 1
    found: dict[tuple[float, float], PillarPoint] = {}
    for index_p in range(config.candidate_grid):
        for index_h in range(config.candidate_grid):
            d_p = config.d_p_min_mm + (config.d_p_max_mm - config.d_p_min_mm) * index_p / span
            d_h = config.d_h_min_mm + (config.d_h_max_mm - config.d_h_min_mm) * index_h / span
            point = PillarPoint(d_p, d_h)
            found[point.key()] = point
    for design in (*config.initial_hf, *config.initial_lf):
        found[design.key()] = design
    return [found[key] for key in sorted(found)]


def _replay_has(config: Experiment3D, design: PillarPoint, fidelity: str) -> bool:
    if config.replay is None:
        return False
    return (*design.key(), fidelity) in config.replay.rows


def _absorb(
    config: Experiment3D,
    dataset: Dataset,
    budget: WallBudget,
    history: list[dict[str, Any]],
    outcome: PillarOutcome,
    *,
    sequential: bool,
) -> None:
    dataset.add(outcome)
    if outcome.status in _FAILURE:
        charged = _recorded_failure_wall_s(outcome)
        budget.charge_known(
            outcome.fidelity,
            charged,
            sequential=sequential,
            failed=outcome.failed,
            imputed=False,
        )
        shown_wall: float | None = charged
        imputed = False
        outcome.cost_imputed = False
    else:
        charged = budget.charge(
            outcome.fidelity,
            measured_wall_s=outcome.measured_wall_s(),
            sequential=sequential,
            failed=outcome.failed,
            imputed=outcome.cost_imputed,
        )
        if outcome.measured_wall_s() is None:
            outcome.cost_imputed = True
        shown_wall = outcome.measured_wall_s()
        imputed = outcome.cost_imputed or shown_wall is None
    x1, x2 = _x(config, outcome)
    history.append(
        {
            "step": len(history) + 1,
            "sequential": sequential,
            "d_p_mm": outcome.d_p_mm,
            "d_h_mm": outcome.d_h_mm,
            "d_f_mm": config.d_f_mm,
            "x1": x1,
            "x2": x2,
            "fidelity": outcome.fidelity,
            "status": outcome.status,
            "failure_reason": outcome.failure_reason,
            "failure_class": outcome.failure_class,
            "lmh": outcome.lmh,
            "cp_average": outcome.cp_average,
            "cp_excess": outcome.cp_excess,
            "pressure_drop_per_length_pa_per_m": outcome.dp_per_l,
            "feasible": _feasible(outcome, config),
            "generate_wall_time_s": outcome.generate_wall_time_s,
            "mesh_wall_time_s": outcome.mesh_wall_time_s,
            "solver_wall_time_s": outcome.solver_wall_time_s,
            "extraction_wall_time_s": outcome.extraction_wall_time_s,
            "wall_s": shown_wall,
            "charged_wall_s": charged,
            "reused_existing": outcome.reused_existing,
            "cost_imputed": imputed,
            "cumulative_wall_s": budget.wall_s,
            "cumulative_hf_equivalent": budget.hf_equivalent,
            "n_lf": budget.n_lf,
            "n_hf": budget.n_hf,
            "best_hf_feasible_lmh": dataset.best_hf_feasible_lmh(config),
        }
    )


def _summary(
    config: Experiment3D,
    dataset: Dataset,
    budget: WallBudget,
    *,
    stopped: bool,
    stop_reason: str | None,
    infrastructure_wall_s: float,
) -> dict[str, Any]:
    incumbent = dataset.best_hf_feasible(config)
    if incumbent is None or not isinstance(incumbent, PillarOutcome):
        recommended: dict[str, Any] = {
            "d_p_mm": None,
            "d_h_mm": None,
            "d_f_mm": config.d_f_mm,
            "hf_lmh": None,
            "hf_cp_average": None,
            "hf_cp_excess": None,
            "hf_pressure_drop_per_length_pa_per_m": None,
        }
    else:
        recommended = {
            "d_p_mm": incumbent.d_p_mm,
            "d_h_mm": incumbent.d_h_mm,
            "d_f_mm": config.d_f_mm,
            "hf_lmh": incumbent.lmh,
            "hf_cp_average": incumbent.cp_average,
            "hf_cp_excess": incumbent.cp_excess,
            "hf_pressure_drop_per_length_pa_per_m": incumbent.dp_per_l,
        }
    failures = [
        {
            "d_p_mm": item.d_p_mm,
            "d_h_mm": item.d_h_mm,
            "fidelity": item.fidelity,
            "status": item.status,
            "failure_reason": item.failure_reason,
            "failure_class": item.failure_class,
        }
        for item in dataset.outcomes
        if item.failed
    ]
    return {
        "method": config.method,
        "mode": config.mode,
        "experiment_id": config.experiment_id,
        "seed": config.seed,
        "run_id": config.run_id,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "infrastructure_wall_s": infrastructure_wall_s,
        "acquisition": HF_METHOD if config.method == METHOD_HF else MF_METHOD,
        "d_f_mm": config.d_f_mm,
        "cp_excess_max": config.cp_excess_max,
        "dp_max_pa_per_m": config.dp_max_pa_per_m,
        "reference_hf_wall_s": config.reference_hf_wall_s,
        "lf_over_hf_cost_ratio": config.lf_over_hf_cost_ratio,
        "budget_hf_equivalent": config.budget_hf_equivalent,
        "cumulative_wall_s": budget.wall_s,
        "cumulative_hf_equivalent": budget.hf_equivalent,
        "n_lf": budget.n_lf,
        "n_hf": budget.n_hf,
        "n_sequential": budget.n_sequential,
        "n_failures": budget.n_failures,
        "failures": failures,
        "final_hf_validated": recommended,
        "note": (
            "offline_replay does not measure new CFD savings."
            if config.mode == MODE_REPLAY
            else (
                "Live costs charge recorded wall time, including failures. "
                "Infrastructure waits are logged and are not charged."
            )
        ),
    }


def _write(
    destination: Path,
    config: Experiment3D,
    dataset: Dataset,
    budget: WallBudget,
    history: list[dict[str, Any]],
    *,
    stopped: bool,
    stop_reason: str | None = None,
    infrastructure_wall_s: float = 0.0,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    summary = _summary(
        config,
        dataset,
        budget,
        stopped=stopped,
        stop_reason=stop_reason,
        infrastructure_wall_s=infrastructure_wall_s,
    )
    payload = {
        "problem": config.problem,
        "budget_hf_equivalent": config.budget_hf_equivalent,
        "max_sequential": config.max_sequential,
        "infrastructure_backoff_s": list(config.infrastructure_backoff_s),
        "infrastructure_cap_s": config.infrastructure_cap_s,
        "objective": "maximize lmh (lmh_window_module)",
        "constraints": [
            f"cp_average - 1 <= {config.cp_excess_max}",
            f"pressure_drop_per_length_pa_per_m <= {config.dp_max_pa_per_m}",
        ],
        "acquisition": summary["acquisition"],
        "hf_equivalent_definition": (
            "cumulative wall seconds (mesh + solver + extraction; "
            "design failures also include generate) / reference_hf_wall_s. "
            "Infrastructure waits are excluded."
        ),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": version("numpy"),
        "botorch": None,
    }
    _write_json(destination / "config.json", payload)
    _write_json(destination / "history.json", history)
    _write_json(destination / "final_summary.json", summary)
    _write_csv(destination / "history.csv", history)
    summary["wrote"] = True
    return summary


def _load_history(
    destination: Path,
    config: Experiment3D,
    dataset: Dataset,
    budget: WallBudget,
) -> list[dict[str, Any]]:
    path = destination / "history.json"
    config_path = destination / "config.json"
    if not path.is_file():
        if config_path.is_file():
            raise ValueError(f"Refusing to resume {destination}: config.json has no history.json.")
        return []
    if not config_path.is_file():
        raise ValueError(f"Refusing to resume {destination}: history.json has no config.json.")
    stored = _read_json_object(config_path, "stored config")
    stored_problem = stored.get("problem")
    if not isinstance(stored_problem, dict):
        raise ValueError(f"Refusing to resume {destination}: stored config has no problem.")
    _require_same_problem(stored_problem, config.problem)
    _require_same_stored_limits(stored, config)
    history = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        raise ValueError(f"Refusing to resume {destination}: history.json is not a list.")
    for index, row in enumerate(history):
        if not isinstance(row, dict):
            raise ValueError(f"history row {index} is not an object.")
        if row.get("step") != index + 1:
            raise ValueError(f"history step {row.get('step')!r} is not {index + 1}.")
        outcome = _outcome_from_history_row(row)
        dataset.add(outcome)
        charged = row.get("charged_wall_s")
        if isinstance(charged, bool) or not isinstance(charged, (int, float)):
            raise ValueError(f"history row {index} is missing charged_wall_s.")
        budget.charge_known(
            outcome.fidelity,
            float(charged),
            sequential=bool(row["sequential"]),
            failed=outcome.failed,
            imputed=bool(row["cost_imputed"]),
        )
    if history:
        last = history[-1]["cumulative_hf_equivalent"]
        if abs(float(last) - budget.hf_equivalent) > 1.0e-6:
            raise ValueError(
                "Refusing to resume: rebuilt HF-equivalent "
                f"{budget.hf_equivalent} does not match history {last}."
            )
    return history


def _require_same_problem(stored: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    left = json.loads(json.dumps(stored, sort_keys=True))
    right = json.loads(json.dumps(current, sort_keys=True))
    if left == right:
        return
    changed = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
    raise ValueError(f"Refusing to resume because the experiment changed: {changed}.")


def _require_same_stored_limits(stored: Mapping[str, Any], config: Experiment3D) -> None:
    """Refuse when the stored run limits differ. Python and platform are ignored."""
    expected = {
        "budget_hf_equivalent": config.budget_hf_equivalent,
        "max_sequential": config.max_sequential,
    }
    changed = [key for key in expected if stored.get(key) != expected[key]]
    if "infrastructure_cap_s" in stored and stored.get("infrastructure_cap_s") != config.infrastructure_cap_s:
        changed.append("infrastructure_cap_s")
    if "infrastructure_backoff_s" in stored and not _same_backoff(
        stored.get("infrastructure_backoff_s"), config.infrastructure_backoff_s
    ):
        changed.append("infrastructure_backoff_s")
    if changed:
        raise ValueError(f"Refusing to resume because the experiment changed: {changed}.")


def _outcome_from_history_row(row: Mapping[str, Any]) -> PillarOutcome:
    return PillarOutcome(
        d_p_mm=_real("d_p_mm", row["d_p_mm"]),
        d_h_mm=_real("d_h_mm", row["d_h_mm"]),
        fidelity=str(row["fidelity"]),
        status=str(row["status"]),
        lmh=_optional_real(row.get("lmh")),
        cp_average=_optional_real(row.get("cp_average")),
        dp_per_l=_optional_real(row.get("pressure_drop_per_length_pa_per_m")),
        mesh_wall_time_s=_optional_real(row.get("mesh_wall_time_s")),
        solver_wall_time_s=_optional_real(row.get("solver_wall_time_s")),
        extraction_wall_time_s=_optional_real(row.get("extraction_wall_time_s")),
        generate_wall_time_s=_optional_real(row.get("generate_wall_time_s")),
        reused_existing=bool(row["reused_existing"]),
        cost_imputed=bool(row["cost_imputed"]),
        failure_reason=_optional_text(row.get("failure_reason")),
        failure_class=_optional_text(row.get("failure_class")),
    )


def _feasible(outcome: PillarOutcome, config: Experiment3D) -> bool:
    if not outcome.trains_gp or outcome.cp_excess is None or outcome.dp_per_l is None:
        return False
    return (
        outcome.cp_excess <= config.cp_excess_max
        and outcome.dp_per_l <= config.dp_max_pa_per_m
    )


def _x(config: Experiment3D, design: PillarPoint | PillarOutcome) -> tuple[float, float]:
    return (
        (float(design.d_p_mm) - config.d_p_min_mm) / (config.d_p_max_mm - config.d_p_min_mm),
        (float(design.d_h_mm) - config.d_h_min_mm) / (config.d_h_max_mm - config.d_h_min_mm),
    )


def _m_min(config: Experiment3D, fidelity: str) -> float:
    try:
        settings = config.fidelity_settings[fidelity]
    except KeyError as exc:
        raise KeyError(f"fidelity {fidelity!r} is not in the fidelity table.") from exc
    return float(settings["m_min"])


def _stopped(destination: Path) -> bool:
    return (destination / STOP_NAME).is_file()


def _replay_row(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "geo_id": record.get("geo_id"),
        "d_p_mm": record["d_p_mm"],
        "d_h_mm": record["d_h_mm"],
        "d_f_mm": record["d_f_mm"],
        "fidelity": record["fidelity"],
        "status": record["status"],
        "failure_reason": record.get("failure_reason"),
        "lmh": record.get("lmh"),
        "cp_average": record.get("cp_average"),
        "pressure_drop_per_length_pa_per_m": record.get("pressure_drop_per_length_pa_per_m"),
        "mesh_wall_time_s": record.get("mesh_wall_time_s"),
        "solver_wall_time_s": record.get("solver_wall_time_s"),
        "extraction_wall_time_s": record.get("extraction_wall_time_s"),
    }


def _replay_hash_payload(table: ReplayTable) -> list[dict[str, Any]]:
    rows = []
    for key in sorted(table.rows):
        item = table.rows[key]
        rows.append(
            {
                "d_p_mm": item.d_p_mm,
                "d_h_mm": item.d_h_mm,
                "fidelity": item.fidelity,
                "status": item.status,
                "lmh": item.lmh,
                "cp_average": item.cp_average,
                "pressure_drop_per_length_pa_per_m": item.dp_per_l,
            }
        )
    return rows


def _outcome_from_replay_row(row: Mapping[str, Any], *, d_f_mm: float) -> PillarOutcome:
    row_d_f = _real("d_f_mm", row["d_f_mm"])
    if abs(row_d_f - d_f_mm) > 1.0e-12:
        raise ValueError(f"Replay row d_f_mm={row_d_f} does not match the experiment d_f_mm={d_f_mm}.")
    fidelity = row.get("fidelity")
    if fidelity not in FIDELITIES:
        raise ValueError(f"Replay fidelity must be one of {FIDELITIES}, got {fidelity!r}.")
    status = row.get("status")
    if status not in _STATUSES:
        raise ValueError(f"Replay status {status!r} is not valid, diverged, invalid, or execution_failed.")
    outcome = PillarOutcome(
        d_p_mm=_real("d_p_mm", row["d_p_mm"]),
        d_h_mm=_real("d_h_mm", row["d_h_mm"]),
        fidelity=str(fidelity),
        status=str(status),
        lmh=_optional_real(row.get("lmh")),
        cp_average=_optional_real(row.get("cp_average")),
        dp_per_l=_optional_real(row.get("pressure_drop_per_length_pa_per_m")),
        mesh_wall_time_s=_optional_real(row.get("mesh_wall_time_s")),
        solver_wall_time_s=_optional_real(row.get("solver_wall_time_s")),
        extraction_wall_time_s=_optional_real(row.get("extraction_wall_time_s")),
        reused_existing=True,
        failure_reason=_optional_text(row.get("failure_reason")),
    )
    if status == STATUS_VALID and not outcome.trains_gp:
        raise ValueError(
            "A valid replay row needs lmh, cp_average, and "
            f"pressure_drop_per_length_pa_per_m. Got d_p_mm={outcome.d_p_mm} "
            f"d_h_mm={outcome.d_h_mm} fidelity={fidelity}."
        )
    return outcome


def _refuse_limit_placeholders(payload: Mapping[str, Any]) -> None:
    pending = [
        name
        for name in ("cp_excess_max", "dp_max_pa_per_m")
        if payload[name] == TBD or payload[name] is None
    ]
    if pending:
        raise ValueError(
            f"{', '.join(pending)} must be a finite number. "
            "The example placeholder is refused."
        )


def _design_box(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, dict):
        raise TypeError("design_box must be an object with d_p_mm and d_h_mm.")
    d_p = _bounds("design_box.d_p_mm", value.get("d_p_mm"))
    d_h = _bounds("design_box.d_h_mm", value.get("d_h_mm"))
    if d_p[0] <= 0.0:
        raise ValueError(f"design_box.d_p_mm minimum must be positive, got {d_p[0]}.")
    if d_h[0] < 0.0:
        raise ValueError(f"design_box.d_h_mm minimum must be >= 0, got {d_h[0]}.")
    return d_p[0], d_p[1], d_h[0], d_h[1]


def _bounds(name: str, value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} must be [min, max].")
    low = _real(f"{name}[0]", value[0])
    high = _real(f"{name}[1]", value[1])
    if low >= high:
        raise ValueError(f"{name} minimum must be below the maximum, got {value!r}.")
    return low, high


def _initial_points(
    name: str,
    value: Any,
    box: Mapping[str, float],
    *,
    allow_empty: bool,
) -> tuple[PillarPoint, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list of designs.")
    if not value and not allow_empty:
        raise ValueError(f"{name} must be a non-empty list.")
    points = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(f"{name}[{index}] must be an object.")
        d_p = _real(f"{name}[{index}].d_p_mm", item.get("d_p_mm"))
        d_h = _real(f"{name}[{index}].d_h_mm", item.get("d_h_mm"))
        if not _inside(d_p, d_h, box["d_p_min_mm"], box["d_p_max_mm"], box["d_h_min_mm"], box["d_h_max_mm"]):
            raise ValueError(f"{name}[{index}] is outside the design box.")
        points.append(PillarPoint(d_p, d_h))
    return tuple(points)


_FIDELITY_ALIAS = {LF: "LF_d", HF: "HF_m"}


def _load_fidelity_settings(path: Path, needed: Sequence[str]) -> dict[str, dict[str, Any]]:
    payload = _read_json_object(path, "fidelity table")
    if not payload:
        raise ValueError(f"fidelity table must be a non-empty JSON object: {path}")
    settings = {}
    for name in needed:
        if name in payload:
            entry = payload[name]
        elif _FIDELITY_ALIAS.get(name) in payload:
            entry = payload[_FIDELITY_ALIAS[name]]
        else:
            raise KeyError(
                f"fidelity {name!r} is not in the fidelity table. High fidelity is not defined."
            )
        if entry == TBD:
            raise ValueError(f"fidelity {name!r} is TBD and is refused.")
        if not isinstance(entry, dict):
            raise TypeError(f"fidelity {name!r} must be mesh settings or {TBD!r}.")
        mesh_id_for_settings(entry)
        settings[name] = dict(entry)
    return settings


def _reference_wall(payload: Mapping[str, Any], data_root: Path) -> tuple[float, str | None]:
    screen = payload.get("screen_csv", None)
    if "screen_csv" in payload and not isinstance(screen, str):
        raise ValueError("screen_csv must be a path.")
    if isinstance(screen, str) and not screen.strip():
        raise ValueError("screen_csv must be a path.")
    value = payload["reference_hf_wall_s"]
    if value == SCREEN_MEDIAN:
        if not isinstance(screen, str):
            raise ValueError("reference_hf_wall_s screen_median requires screen_csv.")
        from ro.mfbo_landscape import cost_medians, read_screen_rows

        path = _resolve_data_path(screen, data_root, "screen_csv")
        medians = cost_medians(read_screen_rows(path))
        return float(medians["hf_wall_median_s"]), screen
    return _positive("reference_hf_wall_s", value), screen if isinstance(screen, str) else None


def _budget_limit(value: Any, reference_hf_wall_s: float) -> float:
    """``11h`` is 11 hours of wall time, in HF-equivalents."""
    if value == BUDGET_11H:
        return TARGET_BUDGET_WALL_H * 3600.0 / reference_hf_wall_s
    return _positive("budget_hf_equivalent", value)


def _optional_cost_proxy(payload: Mapping[str, Any]) -> float | None:
    if "cost_proxy_ratio" not in payload:
        return None
    return _positive("cost_proxy_ratio", payload["cost_proxy_ratio"])


def _resolve_repo_path(value: Any, repo_root: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a path.")
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    if not path.is_file():
        raise FileNotFoundError(f"{name} does not exist: {path}")
    return path


def _resolve_data_path(value: str, data_root: Path, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = data_root / path
    if not path.is_file():
        raise FileNotFoundError(f"{name} does not exist: {path}")
    return path


def _inside(
    d_p_mm: float,
    d_h_mm: float,
    d_p_min: float,
    d_p_max: float,
    d_h_min: float,
    d_h_max: float,
) -> bool:
    return d_p_min - 1.0e-9 <= d_p_mm <= d_p_max + 1.0e-9 and d_h_min - 1.0e-9 <= d_h_mm <= d_h_max + 1.0e-9


def _design_key(d_p_mm: float, d_h_mm: float) -> tuple[float, float]:
    return (round(float(d_p_mm), 6), round(float(d_h_mm), 6))


def _point_dict(design: PillarPoint) -> dict[str, float]:
    return {"d_p_mm": design.d_p_mm, "d_h_mm": design.d_h_mm}


def _sha256_json(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, history: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_HISTORY_FIELDS))
        writer.writeheader()
        for row in history:
            writer.writerow({name: row.get(name) for name in _HISTORY_FIELDS})


def _real(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return number


def _positive(name: str, value: Any) -> float:
    number = _real(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive, got {value!r}.")
    return number


def _optional_real(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    else:
        number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    return value
