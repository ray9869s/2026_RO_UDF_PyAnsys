"""Asymmetric streamwise unit-cell layout for RO spacer CFD domains.

The channel is tiled along x as::

    [n_buffer_in] + [n_active] + [n_buffer_out]

Membrane walls exist only over the active span. This module replaces the
symmetric production triple
``(domain_length_m, n_unit_cells, n_buffer_cells_each_end)``, which cannot
express the current 1+7+2 generation.

``CELL_LENGTH_X_M = 0.003465`` is shared by the legacy 5-cell and current
10-cell families in ``mesh_ledger.csv``. The entrance-decay diagnostic
geometry ``D0817_a45_21c_brg110`` uses a separate third of that pitch
(``CELL_LENGTH_X_D0817_M = 0.001155``) while keeping the same physical
extent and buffer lengths for cell-by-cell comparison.

There is deliberately **no** ``cell_length_y`` here. The spanwise period lives
in the meshing config as ``periodic_shift_y`` in **millimetres** (nominal
3.465 mm). Post-processing does not need that quantity, and it must not be
merged with a metre-scale streamwise cell length: the measured mesh y-extent
for the current geometry is 0.003469131 m against the 3.465 mm nominal shift
(about 0.12% mismatch), so deriving a y period from mesh extent would be
wrong.

``EvaluationWindow`` uses lead/trail exclusions rather than
``(n_sacrificial, n_evaluation)`` so the same window remains valid when
``n_active`` changes; a fixed evaluation count would become invalid.

The registry is keyed by ``(geo_name, mesh_case_name)``. Geo alone is
insufficient: every legacy family has both 3-cell (x extent 0.010395 m) and
5-cell (0.017325 m) meshes. Only 5-cell, 10-cell, and the 30-cell D0817
diagnostic with a known buffer/active split are registered; 3-cell meshes
stay LAYOUT_UNKNOWN.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Optional

# Constants harvested from mesh_ledger.csv (legacy + current 10-cell families
# share this cell length) and from CURRENT / LEGACY buffer-active splits.
CELL_LENGTH_X_M = 0.003465
# D0817 entrance-decay diagnostic: L_f = 0.816708 mm (= 3.465/(3*sqrt(2))),
# theta = 45 deg from the inlet face, so cell length = L_f/cos(45) =
# 1.155000 mm exactly (= 3.465/3). Same physical extent / buffer lengths as
# D2450_a45_7c_brg110 (30 * 0.001155 = 0.03465 m; spacer 0.003465..0.027720 m;
# buffers 3.465 / 6.930 mm). Not a production layout.
CELL_LENGTH_X_D0817_M = 0.001155

MEMBRANE_WALL_BASE_NAMES = ("wall_top_mem", "wall_bottom_mem")
LEGACY_BUFFER_WALL_BASE_NAMES = ("wall_top_buffer", "wall_bottom_buffer")
CURRENT_BUFFER_WALL_BASE_NAMES = (
    "wall_top_buffer_in",
    "wall_top_buffer_out",
    "wall_bottom_buffer_in",
    "wall_bottom_buffer_out",
)

_MATRIX_BASE_TAIL_RE = re.compile(r"^(?P<mesh>.+)_(?P<base>u\d+p\d+_p\d+M)$")
_MATRIX_BASE_ONLY_RE = re.compile(r"^u\d+p\d+_p\d+M$")
# Mesh identity comes from .msh.h5 paths only (.cas.h5 / .dat.h5 are ignored).
# Real Fluent mesh-replace logs always read the template .cas.h5 first, then the
# mesh; quoted paths may wrap across lines with indented continuations.
_MSH_H5_QUOTED_PATH_RE = re.compile(
    r'"(?P<path>[^"]+\.msh\.h5)"',
    re.IGNORECASE,
)
# Trailing case-dir noise that is never part of a mesh_case_name.
_TRAILING_CASE_NOISE_RE = re.compile(
    r"(?:_BAD_UNSTABLE|_BAD_HIGH_CONT_OLD|"
    r"_rerun_attempt_[0-9]+|_attempt_[0-9]+(?:_[0-9]+)*)+$"
)

MESH_RESOLUTION_LOG = "log"
MESH_RESOLUTION_NAME = "name"
_FLOAT_TOKEN = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
# Solver mesh-replace / transcript block: header "Domain Extents:" and each
# axis line MUST carry an explicit "(m)" unit marker. Values are already metres.
# Do NOT reuse _mesh_common._parse_domain_extents_m (meshing log, mm → *1e-3).
_SOLVER_DOMAIN_EXTENTS_RE = re.compile(
    rf"^[ \t]*Domain[ \t]+Extents:[ \t]*\r?\n"
    rf"^[ \t]*x-coordinate:[ \t]*min[ \t]*\(m\)[ \t]*=[ \t]*(?P<x_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*\(m\)[ \t]*=[ \t]*(?P<x_max>{_FLOAT_TOKEN})[ \t]*\r?\n"
    rf"^[ \t]*y-coordinate:[ \t]*min[ \t]*\(m\)[ \t]*=[ \t]*(?P<y_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*\(m\)[ \t]*=[ \t]*(?P<y_max>{_FLOAT_TOKEN})[ \t]*\r?\n"
    rf"^[ \t]*z-coordinate:[ \t]*min[ \t]*\(m\)[ \t]*=[ \t]*(?P<z_min>{_FLOAT_TOKEN})"
    rf"[ \t]*,[ \t]*max[ \t]*\(m\)[ \t]*=[ \t]*(?P<z_max>{_FLOAT_TOKEN})[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_DOMAIN_EXTENTS_HEADER_RE = re.compile(
    r"^[ \t]*Domain[ \t]+[Ee]xtents\.?:?[ \t]*$",
    re.MULTILINE,
)

# Relative tolerance for mesh-coordinate length checks. Diamond_ov020 measures
# 0.017324968 against nominal 5 * 0.003465 (= 0.017325), ~1.8e-6 relative.
DEFAULT_EXTENT_REL_TOL = 1.0e-5


@dataclass(frozen=True)
class DomainLayout:
    """Immutable streamwise layout: buffer / active / buffer cell counts."""

    n_buffer_in: int
    n_active: int
    n_buffer_out: int
    cell_length_x_m: float

    def __post_init__(self) -> None:
        for field_name in ("n_buffer_in", "n_active", "n_buffer_out"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int, got {value!r}.")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value!r}.")
        if self.n_active < 1:
            raise ValueError(f"n_active must be >= 1, got {self.n_active!r}.")
        if isinstance(self.cell_length_x_m, bool) or not isinstance(
            self.cell_length_x_m, (int, float)
        ):
            raise TypeError(
                f"cell_length_x_m must be numeric, got {self.cell_length_x_m!r}."
            )
        if float(self.cell_length_x_m) <= 0.0:
            raise ValueError(
                f"cell_length_x_m must be > 0, got {self.cell_length_x_m!r}."
            )

    @property
    def n_total(self) -> int:
        return self.n_buffer_in + self.n_active + self.n_buffer_out

    @property
    def total_length_m(self) -> float:
        return self.n_total * float(self.cell_length_x_m)

    @property
    def active_length_m(self) -> float:
        return self.n_active * float(self.cell_length_x_m)

    def boundary_positions(self, x0: float) -> list[float]:
        """Return n_total + 1 boundary x positions starting at measured x0."""
        x0_f = float(x0)
        dx = float(self.cell_length_x_m)
        return [x0_f + index * dx for index in range(self.n_total + 1)]

    def spans(self, x0: float) -> list[tuple[str, float, float]]:
        """Return ordered (label, x_min, x_max) for every unit cell."""
        boundaries = self.boundary_positions(x0)
        spans: list[tuple[str, float, float]] = []
        cell_index = 0
        for i in range(self.n_buffer_in):
            spans.append(
                (
                    f"buffer_in_{i + 1}",
                    boundaries[cell_index],
                    boundaries[cell_index + 1],
                )
            )
            cell_index += 1
        for i in range(self.n_active):
            spans.append(
                (
                    f"active_{i + 1}",
                    boundaries[cell_index],
                    boundaries[cell_index + 1],
                )
            )
            cell_index += 1
        for i in range(self.n_buffer_out):
            spans.append(
                (
                    f"buffer_out_{i + 1}",
                    boundaries[cell_index],
                    boundaries[cell_index + 1],
                )
            )
            cell_index += 1
        return spans

    def active_span(self, x0: float) -> tuple[float, float]:
        """Return (x_min, x_max) of the membrane / active region."""
        x0_f = float(x0)
        dx = float(self.cell_length_x_m)
        x_min = x0_f + self.n_buffer_in * dx
        x_max = x_min + self.n_active * dx
        return (x_min, x_max)

    def active_cell_numbers(self) -> list[int]:
        """Global 1-based unit-cell indices of active (membrane) cells."""
        first = self.n_buffer_in + 1
        last = self.n_buffer_in + self.n_active
        return list(range(first, last + 1))


@dataclass(frozen=True)
class EvaluationWindow:
    """Which active cells to trust, as lead/trail exclusions.

    Lead/trail exclusions survive a change in ``n_active``; a fixed evaluation
    count would become invalid when the active span grows or shrinks.
    """

    n_lead_excluded: int
    n_trail_excluded: int

    def __post_init__(self) -> None:
        for field_name in ("n_lead_excluded", "n_trail_excluded"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int, got {value!r}.")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value!r}.")

    def _require_fits(self, layout: DomainLayout) -> None:
        if self.n_lead_excluded + self.n_trail_excluded >= layout.n_active:
            raise ValueError(
                "n_lead_excluded + n_trail_excluded must be < n_active: "
                f"n_lead_excluded={self.n_lead_excluded}, "
                f"n_trail_excluded={self.n_trail_excluded}, "
                f"n_active={layout.n_active}."
            )

    def evaluation_local_indices(self, layout: DomainLayout) -> list[int]:
        """1-based indices within the active span that remain after exclusions."""
        self._require_fits(layout)
        first_local = self.n_lead_excluded + 1
        last_local = layout.n_active - self.n_trail_excluded
        return list(range(first_local, last_local + 1))

    def evaluation_cell_numbers(self, layout: DomainLayout) -> list[int]:
        """Global 1-based unit-cell indices remaining after exclusions."""
        local = self.evaluation_local_indices(layout)
        offset = layout.n_buffer_in
        return [offset + local_index for local_index in local]


@dataclass(frozen=True)
class GeometryLayoutRecord:
    """Layout plus wall base names and evaluation window for one (geo, mesh) pair."""

    layout: DomainLayout
    membrane_wall_base_names: tuple[str, ...]
    buffer_wall_base_names: tuple[str, ...]
    evaluation_window: EvaluationWindow


@dataclass(frozen=True)
class LayoutExtentValidation:
    """Structured result of comparing a layout's nominal length to a measured x span."""

    ok: bool
    expected_length_m: float
    measured_length_m: float
    relative_error: float
    message: str


LEGACY_LAYOUT = DomainLayout(
    n_buffer_in=1,
    n_active=3,
    n_buffer_out=1,
    cell_length_x_m=CELL_LENGTH_X_M,
)
CURRENT_LAYOUT = DomainLayout(
    n_buffer_in=1,
    n_active=7,
    n_buffer_out=2,
    cell_length_x_m=CELL_LENGTH_X_M,
)
# Entrance-decay diagnostic: 3+21+6 at 1/3 the D2450 cell pitch. Same extent
# and buffer lengths as CURRENT_LAYOUT so the two compare cell-by-cell.
D0817_LAYOUT = DomainLayout(
    n_buffer_in=3,
    n_active=21,
    n_buffer_out=6,
    cell_length_x_m=CELL_LENGTH_X_D0817_M,
)

# Lead/trail windows: legacy matches post_config n_inlet_spacer_cells_excluded=1.
# CURRENT / D0817 use lead=3 so aggregate metrics compare on the same physical
# entrance exclusion (D2450 spacer cells 4-7). Open item (do not fix here):
# post_config still has n_inlet_spacer_cells_excluded=1, which makes
# pp_pressure_drop_periodic_per_m ~2% high on D2450 (cell 2 still +9.4% and
# cell 3 +2.5% vs the cells 4-7 mean of 114.68 Pa).
LEGACY_EVALUATION_WINDOW = EvaluationWindow(n_lead_excluded=1, n_trail_excluded=0)
CURRENT_EVALUATION_WINDOW = EvaluationWindow(n_lead_excluded=3, n_trail_excluded=0)
D0817_EVALUATION_WINDOW = EvaluationWindow(n_lead_excluded=3, n_trail_excluded=0)

_LEGACY_RECORD = GeometryLayoutRecord(
    layout=LEGACY_LAYOUT,
    membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
    buffer_wall_base_names=LEGACY_BUFFER_WALL_BASE_NAMES,
    evaluation_window=LEGACY_EVALUATION_WINDOW,
)
_CURRENT_RECORD = GeometryLayoutRecord(
    layout=CURRENT_LAYOUT,
    membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
    buffer_wall_base_names=CURRENT_BUFFER_WALL_BASE_NAMES,
    evaluation_window=CURRENT_EVALUATION_WINDOW,
)
_D0817_RECORD = GeometryLayoutRecord(
    layout=D0817_LAYOUT,
    membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
    buffer_wall_base_names=CURRENT_BUFFER_WALL_BASE_NAMES,
    evaluation_window=D0817_EVALUATION_WINDOW,
)

# 5-cell generation (1 + 3 + 1), extent 0.017325 m — from mesh_ledger.csv.
# 3-cell meshes (extent 0.010395 m) are intentionally NOT registered: their
# buffer/active split is recorded nowhere, and no 3-cell case has ever been
# successfully post-processed. They remain LAYOUT_UNKNOWN.
_LEGACY_5CELL_PAIRS: tuple[tuple[str, str], ...] = (
    ("Diamond_Spacer", "260615_u0p2_p6M"),
    ("Diamond_Spacer", "mesh_max085_min005_cpg5_bl4"),
    ("Diamond_Spacer", "mesh_max085_min006_cpg5_bl4"),
    ("Empty", "260616_u0p2_p6M"),
    ("Empty", "mesh_max085_min005_cpg5_bl4"),
    ("Empty", "mesh_max085_min006_cpg5_bl4"),
    ("Hole_Pillar", "mesh_max085_min005_cpg5_bl4"),
    ("Hole_Pillar", "mesh_max085_min006_cpg5_bl4"),
    ("Multi_Layer_diff", "mesh_max085_min005_cpg5_bl3"),
    ("Multi_Layer_equal", "mesh_max085_min005_cpg5_bl3"),
    ("Multi_Layer_equal", "mesh_max085_min005_cpg5_bl4"),
    ("Pillar", "mesh_max085_min005_cpg5_bl4"),
    ("Pillar", "mesh_max085_min006_cpg5_bl4"),
    ("Sin_SL", "mesh_max085_min005_cpg5_bl4"),
    ("Sin_SL", "mesh_max085_min006_cpg5_bl4"),
    ("Sin_SL", "mesh_max100_min006_cpg3_bl3"),
    ("Sin_SL", "mesh_max100_min006_cpg5_bl4"),
    ("Sin_ST", "mesh_max085_min005_cpg5_bl4"),
    ("Sin_ST", "mesh_max085_min006_cpg5_bl4"),
    ("Sin_ST", "mesh_max100_min006_cpg3_bl3"),
    ("Sin_ST", "mesh_max100_min006_cpg5_bl4"),
    ("Diamond_ov020", "mesh_max085_min006_cpg5_bl4"),
    ("Diamond_ov020", "mesh_max085_min006_cpg7_bl4"),
)

# 10-cell generation (1 + 7 + 2), extent 0.03465 m.
_CURRENT_10CELL_PAIRS: tuple[tuple[str, str], ...] = (
    ("D2450_a45_7c_brg110", "mesh_max085_min006_cpg5_bl4"),
    ("D2450_a45_ov060", "mesh_max085_min006_cpg5_bl4"),
)

# 30-cell entrance-decay diagnostic (3 + 21 + 6) at cell pitch 0.001155 m.
# Same physical extent / buffer lengths as D2450_a45_7c_brg110; not production.
_D0817_30CELL_PAIRS: tuple[tuple[str, str], ...] = (
    ("D0817_a45_21c_brg110", "mesh_max085_min006_cpg5_bl4"),
)


def _build_geometry_layout_registry() -> dict[tuple[str, str], GeometryLayoutRecord]:
    registry: dict[tuple[str, str], GeometryLayoutRecord] = {}
    for geo_name, mesh_case_name in _LEGACY_5CELL_PAIRS:
        registry[(geo_name, mesh_case_name)] = _LEGACY_RECORD
    for geo_name, mesh_case_name in _CURRENT_10CELL_PAIRS:
        registry[(geo_name, mesh_case_name)] = _CURRENT_RECORD
    for geo_name, mesh_case_name in _D0817_30CELL_PAIRS:
        registry[(geo_name, mesh_case_name)] = _D0817_RECORD
    return registry


GEOMETRY_LAYOUT_REGISTRY: Mapping[tuple[str, str], GeometryLayoutRecord] = (
    _build_geometry_layout_registry()
)


def resolve_layout(geo_name: str, mesh_case_name: str) -> GeometryLayoutRecord:
    """Return the layout record for ``(geo_name, mesh_case_name)``, or raise KeyError."""
    key = (geo_name, mesh_case_name)
    try:
        return GEOMETRY_LAYOUT_REGISTRY[key]
    except KeyError as exc:
        known = ", ".join(
            f"{g!r}/{m!r}" for g, m in sorted(GEOMETRY_LAYOUT_REGISTRY)
        )
        raise KeyError(
            f"Unknown geometry/mesh pair geo_name={geo_name!r}, "
            f"mesh_case_name={mesh_case_name!r} for domain layout. "
            f"Known pairs: {known}."
        ) from exc


def layout_post_config_values(geo_name: str, mesh_case_name: str) -> dict[str, object]:
    """Return additive post-config keys for ``(geo_name, mesh_case_name)``.

    Raises KeyError via :func:`resolve_layout` when the pair is unknown.
    Keys use the existing post-config names ``active_membrane_base_names`` and
    ``buffer_wall_base_names`` (not a new membrane_wall_base_names alias).

    Also emits ``domain_length_m``, ``buffer_length_m`` (inlet-side), and
    ``n_unit_cells`` so they stay consistent with the asymmetric layout when
    applied as ``PYFLUENT_POST_OVERRIDES`` onto a base config that still carries
    legacy length defaults. For asymmetric layouts ``n_buffer_cells_each_end``
    is set to ``None`` to clear the inapplicable symmetric key (never a fake
    each-end count).
    """
    record = resolve_layout(geo_name, mesh_case_name)
    layout = record.layout
    values: dict[str, object] = {
        "n_buffer_in": layout.n_buffer_in,
        "n_active": layout.n_active,
        "n_buffer_out": layout.n_buffer_out,
        "cell_length_x_m": layout.cell_length_x_m,
        "active_membrane_base_names": list(record.membrane_wall_base_names),
        "buffer_wall_base_names": list(record.buffer_wall_base_names),
        "domain_length_m": layout.total_length_m,
        "buffer_length_m": layout.n_buffer_in * float(layout.cell_length_x_m),
        "n_unit_cells": layout.n_total,
    }
    if layout.n_buffer_in == layout.n_buffer_out:
        values["n_buffer_cells_each_end"] = layout.n_buffer_in
    else:
        # Clear stale symmetric key from 00_post_config; do not invent a fake.
        values["n_buffer_cells_each_end"] = None
    return values


def mesh_case_name_from_case_dirname(case_name: str) -> Optional[str]:
    """Best-effort single parse of a case directory name (heuristic only).

    Prefer :func:`resolve_mesh_case_name`, which validates candidates against
    the registry and prefers the solver replace log.
    """
    candidates = mesh_case_name_candidates_from_dirname(case_name)
    return candidates[0] if candidates else None


def strip_trailing_case_noise(name: str) -> str:
    """Strip known trailing markers (_BAD_UNSTABLE, _attempt_*, …)."""
    return _TRAILING_CASE_NOISE_RE.sub("", name)


def mesh_case_name_candidates_from_dirname(case_name: str) -> list[str]:
    """Generate ordered mesh_case_name candidates from a case directory name.

    Includes the full name (needed for Diamond_Spacer/260615_u0p2_p6M), the
    suffix after ``__``, the prefix before ``_u0p…``, and variants with known
    trailing markers stripped. Does not validate against the registry.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: Optional[str]) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        ordered.append(value)

    add(case_name)
    stripped = strip_trailing_case_noise(case_name)
    add(stripped)

    if "__" in case_name:
        _base, suffix = case_name.split("__", 1)
        add(suffix)
        add(strip_trailing_case_noise(suffix))
    if "__" in stripped:
        _base, suffix = stripped.split("__", 1)
        add(suffix)
        add(strip_trailing_case_noise(suffix))

    for variant in (case_name, stripped):
        prefix_match = _MATRIX_BASE_TAIL_RE.match(variant)
        if prefix_match is not None:
            mesh = prefix_match.group("mesh")
            add(mesh)
            add(strip_trailing_case_noise(mesh))

    # Drop plain matrix tokens — they are not mesh identities.
    return [c for c in ordered if not _MATRIX_BASE_ONLY_RE.match(c)]


def _normalize_msh_path(raw_path: str) -> str:
    """Collapse whitespace/newlines Fluent may insert inside a quoted path."""
    return "".join(raw_path.split())


def _mesh_case_name_from_msh_h5_path(raw_path: str) -> Optional[str]:
    """Return the parent directory name of a .msh.h5 path, or None."""
    normalized = _normalize_msh_path(raw_path).replace("\\", "/")
    parent_name = PurePosixPath(normalized).parent.name.strip()
    return parent_name or None


def mesh_case_name_from_solver_replace_log(case_dir: Path) -> Optional[str]:
    """Read mesh_case_name from solver_mesh_replace_log_*.txt under case_dir.

    Scans every quoted ``*.msh.h5`` path in the log and uses the **last** match
    (a later mesh replacement wins). ``.cas.h5`` / ``.dat.h5`` reads are
    ignored so the initial template-case load cannot suppress the mesh path.
    """
    case_dir = Path(case_dir)
    if not case_dir.is_dir():
        return None
    log_paths = sorted(case_dir.glob("solver_mesh_replace_log_*.txt"))
    if not log_paths:
        return None
    for log_path in log_paths:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = list(_MSH_H5_QUOTED_PATH_RE.finditer(text))
        if not matches:
            continue
        parent_name = _mesh_case_name_from_msh_h5_path(matches[-1].group("path"))
        if parent_name:
            return parent_name
    return None


def resolve_mesh_case_name(
    case_dir: Path,
    geo_name: str,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a registry-validated mesh_case_name for ``geo_name``.

    Priority:
      1. ``solver_mesh_replace_log_*.txt`` (parent dir of the loaded ``.msh.h5``)
      2. name-derived candidates from the case directory name

    Returns ``(mesh_case_name, source)`` where ``source`` is ``\"log\"`` or
    ``\"name\"``, or ``(None, None)`` when nothing hits the registry. Never
    returns an unvalidated guess.
    """
    case_dir = Path(case_dir)
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: Optional[str], source: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        ordered.append((name, source))

    log_name = mesh_case_name_from_solver_replace_log(case_dir)
    if log_name:
        add(log_name, MESH_RESOLUTION_LOG)
        add(strip_trailing_case_noise(log_name), MESH_RESOLUTION_LOG)

    for candidate in mesh_case_name_candidates_from_dirname(case_dir.name):
        add(candidate, MESH_RESOLUTION_NAME)

    for name, source in ordered:
        if (geo_name, name) in GEOMETRY_LAYOUT_REGISTRY:
            return name, source
    return None, None


def parse_solver_log_domain_extents_m(
    text: str,
) -> tuple[float, float, float, float, float, float]:
    """Parse solver-log ``Domain Extents:`` mins/maxes in metres.

    Requires an explicit ``(m)`` marker on every axis line. Raises ValueError
    if a Domain Extents / Domain extents header is present without those
    markers (so meshing-log mm blocks cannot be silently mis-scaled).
    """
    matches = list(_SOLVER_DOMAIN_EXTENTS_RE.finditer(text))
    if matches:
        match = matches[-1]
        return (
            float(match.group("x_min")),
            float(match.group("x_max")),
            float(match.group("y_min")),
            float(match.group("y_max")),
            float(match.group("z_min")),
            float(match.group("z_max")),
        )
    if _DOMAIN_EXTENTS_HEADER_RE.search(text):
        raise ValueError(
            "Found a Domain Extents / Domain extents header but no axis lines "
            "with explicit '(m)' markers. Refusing to parse meshing-log style "
            "(mm) extents as metres."
        )
    raise ValueError(
        "No solver-log Domain Extents: block with '(m)' markers found."
    )


def validate_layout_against_x_extent(
    layout: DomainLayout,
    x_min: float,
    x_max: float,
    *,
    rel_tol: float = DEFAULT_EXTENT_REL_TOL,
) -> LayoutExtentValidation:
    """Check layout nominal length against measured (x_min, x_max); never raises."""
    expected = float(layout.total_length_m)
    measured = float(x_max) - float(x_min)
    if expected == 0.0:
        rel_err = float("inf") if measured != 0.0 else 0.0
    else:
        rel_err = abs(measured - expected) / abs(expected)
    ok = math.isclose(measured, expected, rel_tol=rel_tol, abs_tol=0.0)
    if ok:
        message = (
            f"measured x extent {measured!r} matches layout nominal "
            f"{expected!r} (rel_err={rel_err:.3e}, rel_tol={rel_tol})."
        )
    else:
        message = (
            f"measured x extent {measured!r} does not match layout nominal "
            f"{expected!r} (rel_err={rel_err:.3e}, rel_tol={rel_tol})."
        )
    return LayoutExtentValidation(
        ok=ok,
        expected_length_m=expected,
        measured_length_m=measured,
        relative_error=rel_err,
        message=message,
    )
