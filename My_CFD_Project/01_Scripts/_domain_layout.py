"""Asymmetric streamwise unit-cell layout for RO spacer CFD domains.

The channel is tiled along x as::

    [n_buffer_in] + [n_active] + [n_buffer_out]

Membrane walls exist only over the active span. This module replaces the
symmetric production triple
``(domain_length_m, n_unit_cells, n_buffer_cells_each_end)``, which cannot
express the current 1+7+2 generation.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# Constants harvested from _tmp_cell_profile.py / _tmp_probe_reduction.py
# (CURRENT generation) and from legacy 1+3+1 configs / mesh-check fixtures.
CELL_LENGTH_X_M = 0.003465

MEMBRANE_WALL_BASE_NAMES = ("wall_top_mem", "wall_bottom_mem")
LEGACY_BUFFER_WALL_BASE_NAMES = ("wall_top_buffer", "wall_bottom_buffer")
CURRENT_BUFFER_WALL_BASE_NAMES = (
    "wall_top_buffer_in",
    "wall_top_buffer_out",
    "wall_bottom_buffer_in",
    "wall_bottom_buffer_out",
)


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
    """Layout plus wall base names for one named geometry."""

    layout: DomainLayout
    membrane_wall_base_names: tuple[str, ...]
    buffer_wall_base_names: tuple[str, ...]


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

_LEGACY_RECORD = GeometryLayoutRecord(
    layout=LEGACY_LAYOUT,
    membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
    buffer_wall_base_names=LEGACY_BUFFER_WALL_BASE_NAMES,
)
_CURRENT_RECORD = GeometryLayoutRecord(
    layout=CURRENT_LAYOUT,
    membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
    buffer_wall_base_names=CURRENT_BUFFER_WALL_BASE_NAMES,
)

# Seeded only for names verified in-repo (mesh-check fixtures / batch_config).
# See module tests for the deliberate omissions.
GEOMETRY_LAYOUT_REGISTRY: Mapping[str, GeometryLayoutRecord] = {
    "D2450_a45_7c_brg110": _CURRENT_RECORD,
    # PROVISIONAL: Sin_ST / Pillar are LEGACY 1+3+1 on total mesh x-extent
    # 0.017325 m alone. Extent does not fix period count — 0.017325 is
    # 5 x 0.003465 but equally 3 x 0.005775 — and neither family's true
    # streamwise period is recorded in this repo. Confirm against the
    # measured membrane wall x-range before trusting these entries.
    "Sin_ST": _LEGACY_RECORD,
    "Pillar": _LEGACY_RECORD,
}


def resolve_layout(geo_name: str) -> GeometryLayoutRecord:
    """Return the layout record for ``geo_name``, or raise KeyError."""
    try:
        return GEOMETRY_LAYOUT_REGISTRY[geo_name]
    except KeyError as exc:
        known = ", ".join(sorted(GEOMETRY_LAYOUT_REGISTRY))
        raise KeyError(
            f"Unknown geometry {geo_name!r} for domain layout. "
            f"Known names: {known}."
        ) from exc


def layout_post_config_values(geo_name: str) -> dict[str, object]:
    """Return additive post-config keys for ``geo_name``.

    Raises KeyError via :func:`resolve_layout` when the geometry is unknown.
    Keys use the existing post-config names ``active_membrane_base_names`` and
    ``buffer_wall_base_names`` (not a new membrane_wall_base_names alias).
    """
    record = resolve_layout(geo_name)
    layout = record.layout
    return {
        "n_buffer_in": layout.n_buffer_in,
        "n_active": layout.n_active,
        "n_buffer_out": layout.n_buffer_out,
        "cell_length_x_m": layout.cell_length_x_m,
        "active_membrane_base_names": list(record.membrane_wall_base_names),
        "buffer_wall_base_names": list(record.buffer_wall_base_names),
    }
