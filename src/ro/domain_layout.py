"""Asymmetric streamwise unit-cell layout for RO spacer CFD domains.

The channel is tiled along x as::

    [n_buffer_in] + [n_active] + [n_buffer_out]

``n_buffer_in`` / ``n_buffer_out`` are zone counts (1 and 2 on the current
split-wall CAD), not a length in pitches. Inlet and outlet buffer lengths
are independent metre fields. ``cell_length_x_m`` is the active-cell pitch
only. ``total_length_m`` is ``buffer_in + n_active * pitch + buffer_out``,
not ``n_total * pitch``.

Membrane walls exist only over the active span. This module replaces the
symmetric production triple
``(domain_length_m, n_unit_cells, n_buffer_cells_each_end)``, which cannot
express the current 1+7+2 generation.

``CELL_LENGTH_X_M = 0.003465`` is the D2450_a45 active pitch, which happens
to equal the inlet buffer. Buffers are 3.465 mm in and 6.93 mm out on the
current diamond CAD; a30/a60 pitches are not integer submultiples of those
lengths.

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

Layout for a live mesh comes only from that mesh's ``manifest.json`` via
:func:`layout_from_mesh_manifest`. Name-keyed lookup is gone;
:func:`resolve_layout` raises rather than guessing 1+3+1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from ro.manifest import read_mesh_manifest, read_run_manifest
from ro.paths import mesh_dir

# D2450_a45 active pitch. Inlet buffer is the same length by coincidence;
# do not treat this constant as a buffer length.
CELL_LENGTH_X_M = 0.003465
BUFFER_LENGTH_IN_M = 0.003465
BUFFER_LENGTH_OUT_M = 0.00693

MEMBRANE_WALL_BASE_NAMES = ("wall_top_mem", "wall_bottom_mem")
LEGACY_BUFFER_WALL_BASE_NAMES = ("wall_top_buffer", "wall_bottom_buffer")
CURRENT_BUFFER_WALL_BASE_NAMES = (
    "wall_top_buffer_in",
    "wall_top_buffer_out",
    "wall_bottom_buffer_in",
    "wall_bottom_buffer_out",
)

# Mesh identity comes from .msh.h5 paths only (.cas.h5 / .dat.h5 are ignored).
# Real Fluent mesh-replace logs always read the template .cas.h5 first, then the
# mesh; quoted paths may wrap across lines with indented continuations.
_MSH_H5_QUOTED_PATH_RE = re.compile(
    r'"(?P<path>[^"]+\.msh\.h5)"',
    re.IGNORECASE,
)
# Campaign leaf: .../meshes/{family}/{geo_id}/{mesh_id}/{file}.msh.h5
_CAMPAIGN_MSH_IDENTITY_RE = re.compile(
    r"(?:^|/)meshes/(?P<family>[^/]+)/(?P<geo_id>[^/]+)/(?P<mesh_id>[^/]+)/"
    r"[^/]+\.msh\.h5$",
    re.IGNORECASE,
)
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
# 31 production leaves (workstation 2026-09-11): max |x - layout.total_length_m|
# / layout is ~1.6e-7. 1e-5 keeps ~60x margin vs that noise and is ~1000x
# below a one-cell error. y/z are not gated (CAD-scale residuals).
DEFAULT_EXTENT_REL_TOL = 1.0e-5


def _require_positive_length(field_name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric, got {value!r}.")
    length = float(value)
    if length <= 0.0:
        raise ValueError(f"{field_name} must be > 0, got {value!r}.")
    return length


@dataclass(frozen=True)
class DomainLayout:
    """Immutable streamwise layout: zone counts, active pitch, buffer lengths."""

    n_buffer_in: int
    n_active: int
    n_buffer_out: int
    cell_length_x_m: float
    buffer_length_in_m: float
    buffer_length_out_m: float

    def __post_init__(self) -> None:
        for field_name in ("n_buffer_in", "n_active", "n_buffer_out"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int, got {value!r}.")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value!r}.")
        if self.n_active < 1:
            raise ValueError(f"n_active must be >= 1, got {self.n_active!r}.")
        if self.n_buffer_in < 1:
            raise ValueError(
                "n_buffer_in must be >= 1 to split buffer_length_in_m, "
                f"got {self.n_buffer_in!r}."
            )
        if self.n_buffer_out < 1:
            raise ValueError(
                "n_buffer_out must be >= 1 to split buffer_length_out_m, "
                f"got {self.n_buffer_out!r}."
            )
        _require_positive_length("cell_length_x_m", self.cell_length_x_m)
        _require_positive_length("buffer_length_in_m", self.buffer_length_in_m)
        _require_positive_length("buffer_length_out_m", self.buffer_length_out_m)

    @property
    def n_total(self) -> int:
        return self.n_buffer_in + self.n_active + self.n_buffer_out

    @property
    def total_length_m(self) -> float:
        return (
            float(self.buffer_length_in_m)
            + self.n_active * float(self.cell_length_x_m)
            + float(self.buffer_length_out_m)
        )

    @property
    def active_length_m(self) -> float:
        return self.n_active * float(self.cell_length_x_m)

    def boundary_positions(self, x0: float) -> list[float]:
        """Return n_total + 1 boundary x positions starting at measured x0.

        Inlet buffer, then ``n_active`` pitch steps, then outlet buffer.
        Each buffer is split equally across its zone count (so D2450_a45
        with ``n_buffer_out=2`` keeps the interior outlet plane).
        """
        x = float(x0)
        boundaries = [x]
        dx_in = float(self.buffer_length_in_m) / self.n_buffer_in
        for _ in range(self.n_buffer_in):
            x += dx_in
            boundaries.append(x)
        dx = float(self.cell_length_x_m)
        for _ in range(self.n_active):
            x += dx
            boundaries.append(x)
        dx_out = float(self.buffer_length_out_m) / self.n_buffer_out
        for _ in range(self.n_buffer_out):
            x += dx_out
            boundaries.append(x)
        return boundaries

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
        x_min = x0_f + float(self.buffer_length_in_m)
        x_max = x_min + self.n_active * float(self.cell_length_x_m)
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
    """Layout plus wall base names and evaluation window from a mesh manifest."""

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
    buffer_length_in_m=BUFFER_LENGTH_IN_M,
    buffer_length_out_m=BUFFER_LENGTH_IN_M,
)
CURRENT_LAYOUT = DomainLayout(
    n_buffer_in=1,
    n_active=7,
    n_buffer_out=2,
    cell_length_x_m=CELL_LENGTH_X_M,
    buffer_length_in_m=BUFFER_LENGTH_IN_M,
    buffer_length_out_m=BUFFER_LENGTH_OUT_M,
)

# Lead/trail windows: legacy matches the old post_config alias
# n_inlet_spacer_cells_excluded=1. CURRENT uses lead=3 as a D2450-derived
# cell-count convention; whether decay follows cell count or an absolute
# length is still open. layout_post_config_values emits the window; stock
# post_config leaves it unset so a direct run cannot silently score lead=1.
LEGACY_EVALUATION_WINDOW = EvaluationWindow(n_lead_excluded=1, n_trail_excluded=0)
CURRENT_EVALUATION_WINDOW = EvaluationWindow(n_lead_excluded=3, n_trail_excluded=0)

def layout_from_mesh_manifest(mesh_directory: Path) -> GeometryLayoutRecord:
    """Build a layout record from ``mesh_directory/manifest.json``.

    The mesh manifest is the only source of layout. Missing or invalid JSON
    raises :class:`ro.manifest.ManifestError` via :func:`read_mesh_manifest`.
    """
    payload = read_mesh_manifest(mesh_directory)
    layout = DomainLayout(
        n_buffer_in=int(payload["n_buffer_in"]),
        n_active=int(payload["n_active_cells"]),
        n_buffer_out=int(payload["n_buffer_out"]),
        cell_length_x_m=float(payload["cell_length_x_m"]),
        buffer_length_in_m=float(payload["buffer_length_in_m"]),
        buffer_length_out_m=float(payload["buffer_length_out_m"]),
    )
    return GeometryLayoutRecord(
        layout=layout,
        membrane_wall_base_names=tuple(payload["membrane_wall_base_names"]),
        buffer_wall_base_names=tuple(payload["buffer_wall_base_names"]),
        evaluation_window=EvaluationWindow(
            n_lead_excluded=int(payload["n_lead_excluded"]),
            n_trail_excluded=int(payload["n_trail_excluded"]),
        ),
    )


def layout_from_run_directory(
    run_directory: Path,
) -> tuple[GeometryLayoutRecord, dict]:
    """Layout for a run: read the run manifest, then its mesh manifest.

    Missing or stale manifests raise ManifestError. Identification is the
    manifest payload, not directory-name parsing.
    """
    payload = read_run_manifest(run_directory)
    mesh_directory = mesh_dir(
        payload["family"], payload["geo_id"], payload["mesh_id"]
    )
    return layout_from_mesh_manifest(mesh_directory), payload


def resolve_layout(geo_name: str, mesh_case_name: str) -> GeometryLayoutRecord:
    """Name-keyed layout lookup is gone. Always raises.

    Call :func:`layout_from_mesh_manifest` with the mesh directory instead.
    ``geo_name`` and ``mesh_case_name`` are unused; they remain in the
    signature so leftover callers fail here instead of guessing 1+3+1.
    """
    raise RuntimeError("layout comes from the mesh manifest")


def layout_post_config_values(record: GeometryLayoutRecord) -> dict[str, object]:
    """Return additive post-config keys for a layout record.

    Keys use the existing post-config names ``active_membrane_base_names`` and
    ``buffer_wall_base_names`` (not a new membrane_wall_base_names alias).

    Also emits ``domain_length_m``, ``buffer_length_m`` (inlet-side), and
    ``n_unit_cells`` so they stay consistent with the asymmetric layout when
    applied as ``PYFLUENT_POST_OVERRIDES`` onto a base config that still carries
    legacy length defaults. For asymmetric layouts ``n_buffer_cells_each_end``
    is set to ``None`` to clear the inapplicable symmetric key (never a fake
    each-end count).

    Evaluation-window keys ``n_lead_excluded`` and ``n_trail_excluded`` come
    from the mesh manifest. ``n_inlet_spacer_cells_excluded`` is the post-config
    alias of lead exclusion and is set to the same value so leftover readers
    cannot keep the stock default of 1.
    """
    layout = record.layout
    window = record.evaluation_window
    values: dict[str, object] = {
        "n_buffer_in": layout.n_buffer_in,
        "n_active": layout.n_active,
        "n_buffer_out": layout.n_buffer_out,
        "cell_length_x_m": layout.cell_length_x_m,
        "buffer_length_in_m": layout.buffer_length_in_m,
        "buffer_length_out_m": layout.buffer_length_out_m,
        "active_membrane_base_names": list(record.membrane_wall_base_names),
        "buffer_wall_base_names": list(record.buffer_wall_base_names),
        "domain_length_m": layout.total_length_m,
        "buffer_length_m": float(layout.buffer_length_in_m),
        "n_unit_cells": layout.n_total,
        "n_lead_excluded": window.n_lead_excluded,
        "n_trail_excluded": window.n_trail_excluded,
        "n_inlet_spacer_cells_excluded": window.n_lead_excluded,
    }
    if layout.n_buffer_in == layout.n_buffer_out:
        values["n_buffer_cells_each_end"] = layout.n_buffer_in
    else:
        # Clear stale symmetric key from post_config; do not invent a fake.
        values["n_buffer_cells_each_end"] = None
    return values


def _normalize_msh_path(raw_path: str) -> str:
    """Collapse whitespace/newlines Fluent may insert inside a quoted path."""
    return "".join(raw_path.split())


def _mesh_case_name_from_msh_h5_path(raw_path: str) -> Optional[str]:
    """Return the parent directory name of a .msh.h5 path, or None."""
    normalized = _normalize_msh_path(raw_path).replace("\\", "/")
    parent_name = PurePosixPath(normalized).parent.name.strip()
    return parent_name or None


def campaign_mesh_identity_from_msh_h5_path(raw_path: str):
    """Return {family, geo_id, mesh_id} from a campaign msh path, or None.

    Parent-directory mesh_id alone is not enough: the same mesh_id string is
    reused across geos.
    """
    normalized = _normalize_msh_path(raw_path).replace("\\", "/")
    match = _CAMPAIGN_MSH_IDENTITY_RE.search(normalized)
    if match is None:
        return None
    return {
        "family": match.group("family"),
        "geo_id": match.group("geo_id"),
        "mesh_id": match.group("mesh_id"),
    }


def current_solver_replace_log_path(run_directory: Path, run_id: str) -> Path:
    """Canonical current-attempt replace log (not ``__attemptN`` archives)."""
    return Path(run_directory) / f"solver_mesh_replace_log_{run_id}.txt"


def inspect_replace_log_identity(*, log_path: Path, mesh_directory: Path):
    """Status of one explicit replace log vs the mesh manifest.

    Returns (status, reason) where status is NOT_CHECKED, PASS, or REJECT.
    Missing log is NOT_CHECKED. Unreadable log, no campaign triple, or a
    family/geo_id/mesh_id mismatch is REJECT. This is not byte identity.
    """
    log_path = Path(log_path)
    if not log_path.is_file():
        return "NOT_CHECKED", None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "REJECT", f"unreadable replace log {log_path}: {exc}"
    matches = list(_MSH_H5_QUOTED_PATH_RE.finditer(text))
    if not matches:
        return (
            "REJECT",
            f"replace log {log_path} has no quoted .msh.h5 path",
        )
    identity = campaign_mesh_identity_from_msh_h5_path(matches[-1].group("path"))
    if identity is None:
        return (
            "REJECT",
            f"replace log {log_path} msh path is not "
            "meshes/{{family}}/{{geo_id}}/{{mesh_id}}/",
        )
    payload = read_mesh_manifest(mesh_directory)
    expected = {
        "family": payload["family"],
        "geo_id": payload["geo_id"],
        "mesh_id": payload["mesh_id"],
    }
    if identity != expected:
        return (
            "REJECT",
            "replace log mesh identity "
            f"{identity!r} does not match manifest {expected!r}.",
        )
    return "PASS", None


def mesh_case_name_from_solver_replace_log(case_dir: Path) -> Optional[str]:
    """Read mesh_case_name from solver_mesh_replace_log_*.txt under case_dir.

    Scans every quoted ``*.msh.h5`` path in the log and uses the **last** match
    (a later mesh replacement wins). ``.cas.h5`` / ``.dat.h5`` reads are
    ignored so the initial template-case load cannot suppress the mesh path.

    Glob + first matching log is not a live gate (Astra V-05): it can pick the
    wrong file, skip unreadable logs, and only sees mesh_id. Use
    ``inspect_replace_log_identity`` on the current-attempt path instead.
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


def assert_replace_log_matches_mesh_manifest(
    case_dir: Path,
    mesh_directory: Path,
) -> None:
    """Raise if a solver replace log names a different mesh than the manifest.

    No replace log is not a mismatch. A quoted ``*.msh.h5`` parent directory
    that differs from the manifest ``mesh_id`` is a mismatch.
    """
    log_name = mesh_case_name_from_solver_replace_log(case_dir)
    if log_name is None:
        return
    payload = read_mesh_manifest(mesh_directory)
    mesh_id = payload["mesh_id"]
    if log_name != mesh_id:
        raise RuntimeError(
            "solver replace log mesh parent "
            f"{log_name!r} does not match manifest mesh_id {mesh_id!r}."
        )


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


def require_layout_matches_measured_x_extent(
    layout: DomainLayout,
    domain_extent_x_m: object,
    *,
    rel_tol: float = DEFAULT_EXTENT_REL_TOL,
) -> LayoutExtentValidation:
    """Raise unless measured x length matches ``layout.total_length_m``.

    CAD origin is 0: ``x_min=0``, ``x_max=domain_extent_x_m``. y/z are not
    checked. Missing or null ``domain_extent_x_m`` is a failure, not a skip.
    The identity is ``total_length_m``, not ``n_total * cell_length_x_m``.
    """
    if domain_extent_x_m is None:
        raise ValueError(
            "domain_extent_x_m is missing or null; measured x extent is "
            "required before layout validation. Backfill from the mesh log "
            "or remesh."
        )
    if isinstance(domain_extent_x_m, bool) or not isinstance(
        domain_extent_x_m, (int, float)
    ):
        raise TypeError(
            f"domain_extent_x_m must be numeric, got {domain_extent_x_m!r}."
        )
    result = validate_layout_against_x_extent(
        layout, 0.0, float(domain_extent_x_m), rel_tol=rel_tol
    )
    if not result.ok:
        raise ValueError(result.message)
    return result


def require_mesh_manifest_x_extent_matches_layout(
    mesh_directory: Path,
    *,
    rel_tol: float = DEFAULT_EXTENT_REL_TOL,
) -> LayoutExtentValidation:
    """Read a mesh manifest and raise unless its x extent matches layout."""
    payload = read_mesh_manifest(mesh_directory)
    layout = DomainLayout(
        n_buffer_in=int(payload["n_buffer_in"]),
        n_active=int(payload["n_active_cells"]),
        n_buffer_out=int(payload["n_buffer_out"]),
        cell_length_x_m=float(payload["cell_length_x_m"]),
        buffer_length_in_m=float(payload["buffer_length_in_m"]),
        buffer_length_out_m=float(payload["buffer_length_out_m"]),
    )
    return require_layout_matches_measured_x_extent(
        layout,
        payload.get("domain_extent_x_m"),
        rel_tol=rel_tol,
    )
