"""Shared helpers for Fluent report surfaces and unit-cell diagnostics."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    CP_SPREAD_AREA_QUANTILE_HI,
    CP_SPREAD_AREA_QUANTILE_LO,
    FACET_MIN_REJECT_AREA_FRAC,
    FACET_MIN_TARGET_AREA_FRAC,
    bisect_area_fraction_threshold,
    canonical_rescale_factor,
    cp_l1_gu2017,
    facet_min_check_threshold,
    film_theory_cp_perm_mol_m3,
    midplane_window_bulk_aggregate,
    resolve_area_backed_minimum,
    window_area_weighted_average,
    window_pointwise_max,
)
from ro.domain_layout import DomainLayout, EvaluationWindow


def list_named_object_names(named_object, object_label=""):
    """Return names from a PyFluent named object."""
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass

    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(state)
        if isinstance(state, list):
            return list(state)
    except Exception:
        pass

    try:
        names = named_object.list_1()
        if names is None:
            print(f"Warning: {object_label}.list_1() returned None.")
            return []
        return list(names)
    except Exception as exc:
        print(f"Could not list names for {object_label}. Error: {exc}")
        return []


def create_x_normal_plane(solver_obj, surface_name, x_value_m):
    """Create an x-normal iso-surface, replacing an existing surface of the same name."""
    settings_error = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list_named_object_names(
            iso_group,
            "results.surfaces.iso_surface",
        )
        if surface_name in existing:
            try:
                iso_group.delete(surface_name)
                print(f"Deleted existing iso-surface: {surface_name}")
            except Exception as exc:
                print(f"Could not delete iso-surface {surface_name}: {exc}")

        iso_group.create(surface_name)
        iso_group[surface_name].field = "x-coordinate"
        iso_group[surface_name].iso_values = [x_value_m]
        print(
            f"Created iso-surface '{surface_name}' at x = "
            f"{x_value_m:.6e} m (settings API)"
        )
        return
    except Exception as exc:
        settings_error = exc
        print(f"Settings API failed for iso-surface '{surface_name}': {exc}")

    try:
        solver_obj.tui.surface.iso_surface(
            "x-coordinate",
            surface_name,
            "()",
            "()",
            str(x_value_m),
            "0",
        )
        print(
            f"Created iso-surface '{surface_name}' at x = "
            f"{x_value_m:.6e} m (TUI fallback)"
        )
    except Exception as tui_error:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {settings_error}. TUI error: {tui_error}"
        ) from tui_error



def create_z_normal_plane(solver_obj, surface_name, z_value_m):
    """Create a z-normal iso-surface, replacing an existing surface of the same name."""
    settings_error = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list_named_object_names(
            iso_group,
            "results.surfaces.iso_surface",
        )
        if surface_name in existing:
            try:
                iso_group.delete(surface_name)
                print(f"Deleted existing iso-surface: {surface_name}")
            except Exception as exc:
                print(f"Could not delete iso-surface {surface_name}: {exc}")

        iso_group.create(surface_name)
        iso_group[surface_name].field = "z-coordinate"
        iso_group[surface_name].iso_values = [z_value_m]
        print(
            f"Created iso-surface '{surface_name}' at z = "
            f"{z_value_m:.6e} m (settings API)"
        )
        return
    except Exception as exc:
        settings_error = exc
        print(f"Settings API failed for iso-surface '{surface_name}': {exc}")

    try:
        solver_obj.tui.surface.iso_surface(
            "z-coordinate",
            surface_name,
            "()",
            "()",
            str(z_value_m),
            "0",
        )
        print(
            f"Created iso-surface '{surface_name}' at z = "
            f"{z_value_m:.6e} m (TUI fallback)"
        )
    except Exception as tui_error:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {settings_error}. TUI error: {tui_error}"
        ) from tui_error


def measure_fluid_z_bounds_m(solver, setup, fluid_zone_names):
    """Return (z_min_m, z_max_m) from reduction min/max of z-coordinate.

    Uses ``solver.fields.reduction`` on fluid cell zones. The Fluent settings
    tree does not expose domain min/max at the iso-surface creation site, and
    the mesh manifest only stores ``domain_extent_z_m`` as a length (max-min),
    so a live reduction is the origin-agnostic source of the mid-plane.
    """
    if not fluid_zone_names:
        raise ValueError("fluid_zone_names must be non-empty to measure z bounds.")
    locations = fluid_zone_reduction_locations(setup, fluid_zone_names)
    reduction = solver.fields.reduction
    z_min = float(
        reduction.minimum(expression="z-coordinate", locations=locations)
    )
    z_max = float(
        reduction.maximum(expression="z-coordinate", locations=locations)
    )
    if not (math.isfinite(z_min) and math.isfinite(z_max)):
        raise RuntimeError(
            f"Non-finite fluid z bounds from reduction: z_min={z_min!r}, "
            f"z_max={z_max!r}."
        )
    if z_max <= z_min:
        raise RuntimeError(
            f"Degenerate fluid z bounds from reduction: z_min={z_min!r}, "
            f"z_max={z_max!r}."
        )
    return z_min, z_max


def resolve_channel_midplane_z_m(
    solver=None,
    setup=None,
    fluid_zone_names=None,
    *,
    z_min_m=None,
    z_max_m=None,
    fallback_z_m=0.0,
):
    """Origin-agnostic channel mid-plane z [m].

    Preference order:
      1. Explicit ``z_min_m`` / ``z_max_m`` (caller-measured bounds)
      2. Live ``fields.reduction`` min/max of ``z-coordinate`` on fluid zones
      3. ``fallback_z_m`` (default 0.0 = campaign channel-centred origin)

    Returns ``(z_mid_m, diagnostics_dict)``. Never uses a candidate list of
    ``h/2`` vs ``0`` — that trap permanently selects the first iso-value that
    happens to lie inside ``[z_min, z_max]``.
    """
    diag: dict[str, Any] = {
        "source": None,
        "z_min_m": None,
        "z_max_m": None,
        "z_mid_m": None,
        "fallback_z_m": float(fallback_z_m),
        "measure_error": None,
    }
    if z_min_m is not None and z_max_m is not None:
        z0 = float(z_min_m)
        z1 = float(z_max_m)
        if not (math.isfinite(z0) and math.isfinite(z1) and z1 > z0):
            raise ValueError(
                f"Invalid explicit z bounds: z_min_m={z_min_m!r}, "
                f"z_max_m={z_max_m!r}."
            )
        z_mid = 0.5 * (z0 + z1)
        diag.update(
            source="explicit_bounds",
            z_min_m=z0,
            z_max_m=z1,
            z_mid_m=z_mid,
        )
        return z_mid, diag

    if solver is not None and setup is not None and fluid_zone_names:
        try:
            z0, z1 = measure_fluid_z_bounds_m(solver, setup, fluid_zone_names)
            z_mid = 0.5 * (z0 + z1)
            diag.update(
                source="fluid_reduction",
                z_min_m=z0,
                z_max_m=z1,
                z_mid_m=z_mid,
            )
            return z_mid, diag
        except Exception as exc:
            diag["measure_error"] = f"{type(exc).__name__}: {exc}"

    z_mid = float(fallback_z_m)
    diag.update(source="fallback_centred_origin", z_mid_m=z_mid)
    return z_mid, diag


def create_channel_midplane_plane(
    solver,
    *,
    setup=None,
    fluid_zone_names=None,
    z_min_m=None,
    z_max_m=None,
    surface_name=None,
    fallback_z_m=0.0,
):
    """Create the channel mid-plane iso-surface; return (name, z_mid, diag).

    Mid-plane z is ``0.5 * (z_min + z_max)`` from measured fluid bounds when
    available. Falls back to ``fallback_z_m`` (default 0.0). Pair with
    ``assert_midplane_c_b_matches_boundary_mixing_cup`` in report extract so a
    silent wall-plane sample cannot land in the canonical CP denominator.
    """
    z_mid, diag = resolve_channel_midplane_z_m(
        solver,
        setup,
        fluid_zone_names,
        z_min_m=z_min_m,
        z_max_m=z_max_m,
        fallback_z_m=fallback_z_m,
    )
    if surface_name is None:
        surface_name = (
            f"pp_plane_zc_{abs(z_mid):.7f}".replace(".", "p")
        )
    create_z_normal_plane(solver, surface_name, z_mid)
    diag = dict(diag)
    diag["plane_name"] = surface_name
    return surface_name, z_mid, diag


# Default relative tolerance for mid-plane c_b vs x-normal mixing-cup.
# Wall-adjacent mis-placement on D2450_a45 disagreed by ~3.5%; legitimate
# mid-plane vs local boundary mixing-cup stay within a few tenths of a percent.
MIDPLANE_CB_MIXING_CUP_REL_TOL = 0.005


def assert_midplane_c_b_matches_boundary_mixing_cup(
    c_b_by_cell_mol_per_m3,
    mixing_cup_mol_per_m3_by_boundary,
    cell_numbers,
    *,
    rel_tol=MIDPLANE_CB_MIXING_CUP_REL_TOL,
):
    """Raise if mid-plane c_b disagrees with flanking x-normal mixing-cups.

    For each cell N, compare mid-plane c_b to the mean of the mixing-cup molar
    concentrations on boundaries N-1 and N. Those x-normal cups are an
    independent bulk measure; a wall-placed "mid-plane" fails this check.
    """
    if not cell_numbers:
        raise ValueError("cell_numbers must be non-empty.")
    failures = []
    for cell_number in cell_numbers:
        if cell_number not in c_b_by_cell_mol_per_m3:
            raise KeyError(
                f"c_b_by_cell_mol_per_m3 missing evaluation cell {cell_number}."
            )
        left = mixing_cup_mol_per_m3_by_boundary.get(cell_number - 1)
        right = mixing_cup_mol_per_m3_by_boundary.get(cell_number)
        if left is None or right is None:
            raise KeyError(
                f"mixing_cup_mol_per_m3_by_boundary missing boundaries "
                f"{cell_number - 1} and/or {cell_number} for cell {cell_number}."
            )
        left_f = float(left)
        right_f = float(right)
        if left_f <= 0.0 or right_f <= 0.0:
            raise ValueError(
                f"Non-positive mixing-cup molar concentration for cell "
                f"{cell_number} boundaries: left={left_f!r}, right={right_f!r}."
            )
        ref = 0.5 * (left_f + right_f)
        c_b = float(c_b_by_cell_mol_per_m3[cell_number])
        rel_err = abs(c_b - ref) / ref
        if rel_err > float(rel_tol):
            failures.append(
                f"cell {cell_number}: c_b={c_b:.6g} vs mixing-cup mean "
                f"{ref:.6g} (boundaries {cell_number - 1}/{cell_number} = "
                f"{left_f:.6g}/{right_f:.6g}), rel_err={rel_err:.4%} "
                f"> tol={float(rel_tol):.4%}"
            )
    if failures:
        raise RuntimeError(
            "Mid-plane c_b disagrees with x-normal cell-boundary mixing-cup "
            "concentrations; the mid-plane iso-surface is likely not at the "
            "channel centre (wall-adjacent sampling). "
            + "; ".join(failures)
        )


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}.")


def unit_cell_boundary_positions(domain_x_min_m, domain_length_m, n_unit_cells):
    """Return all unit-cell boundary x positions, including both domain ends."""
    _require_positive_int("n_unit_cells", n_unit_cells)
    if isinstance(domain_length_m, bool) or not isinstance(
        domain_length_m,
        (int, float),
    ):
        raise TypeError(
            f"domain_length_m must be numeric, got {domain_length_m!r}."
        )
    if domain_length_m <= 0.0:
        raise ValueError(
            f"domain_length_m must be positive, got {domain_length_m!r}."
        )

    cell_length_m = float(domain_length_m) / n_unit_cells
    return [
        float(domain_x_min_m) + boundary_index * cell_length_m
        for boundary_index in range(n_unit_cells + 1)
    ]


def spacer_cell_numbers(n_unit_cells, n_buffer_cells_each_end):
    """Return one-based unit-cell numbers between the inlet and outlet buffers."""
    _require_positive_int("n_unit_cells", n_unit_cells)
    if isinstance(n_buffer_cells_each_end, bool) or not isinstance(
        n_buffer_cells_each_end,
        int,
    ):
        raise TypeError(
            "n_buffer_cells_each_end must be an integer, "
            f"got {n_buffer_cells_each_end!r}."
        )
    if n_buffer_cells_each_end < 0:
        raise ValueError(
            "n_buffer_cells_each_end must be non-negative, "
            f"got {n_buffer_cells_each_end!r}."
        )
    if 2 * n_buffer_cells_each_end >= n_unit_cells:
        raise ValueError(
            "Buffer cells must leave at least one spacer cell: "
            f"n_unit_cells={n_unit_cells}, "
            f"n_buffer_cells_each_end={n_buffer_cells_each_end}."
        )

    return list(
        range(
            n_buffer_cells_each_end + 1,
            n_unit_cells - n_buffer_cells_each_end + 1,
        )
    )


def validate_unit_cell_layout(
    domain_length_m,
    buffer_length_m,
    n_unit_cells,
    n_buffer_cells_each_end,
):
    """Validate that legacy buffer length and unit-cell counts describe one layout."""
    spacer_cells = spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    )
    expected_buffer_length_m = (
        float(domain_length_m)
        / n_unit_cells
        * n_buffer_cells_each_end
    )
    if not math.isclose(
        float(buffer_length_m),
        expected_buffer_length_m,
        rel_tol=1.0e-9,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            "buffer_length_m is inconsistent with the unit-cell layout: "
            f"buffer_length_m={buffer_length_m!r}, "
            f"expected={expected_buffer_length_m!r}, "
            f"n_unit_cells={n_unit_cells}, "
            f"n_buffer_cells_each_end={n_buffer_cells_each_end}."
        )
    return spacer_cells


@dataclass(frozen=True)
class ScoringLayoutGeometry:
    """Geometry derived from an asymmetric DomainLayout for report scoring."""

    layout: DomainLayout
    domain_x_min_m: float
    domain_length_m: float
    spacer_x_in_m: float
    spacer_x_out_m: float
    spacer_length_m: float
    unit_cell_boundary_x_m: list[float]
    spacer_cells: list[int]


_LAYOUT_REL_TOL = 1.0e-9
_LAYOUT_ABS_TOL = 1.0e-12


def scoring_geometry_from_layout(
    layout: DomainLayout,
    domain_x_min_m: float,
) -> ScoringLayoutGeometry:
    """Derive plane positions and active-cell indices from DomainLayout."""
    x0 = float(domain_x_min_m)
    spacer_x_in_m, spacer_x_out_m = layout.active_span(x0)
    return ScoringLayoutGeometry(
        layout=layout,
        domain_x_min_m=x0,
        domain_length_m=float(layout.total_length_m),
        spacer_x_in_m=float(spacer_x_in_m),
        spacer_x_out_m=float(spacer_x_out_m),
        spacer_length_m=float(layout.active_length_m),
        unit_cell_boundary_x_m=layout.boundary_positions(x0),
        spacer_cells=layout.active_cell_numbers(),
    )


def _config_has_layout_value(cfg: Any, name: str) -> bool:
    """True when cfg defines ``name`` with a non-None value."""
    if not hasattr(cfg, name):
        return False
    return getattr(cfg, name) is not None


def _require_config_attr(cfg: Any, name: str) -> Any:
    if not hasattr(cfg, name):
        raise AttributeError(f"Missing required layout config key: {name!r}.")
    value = getattr(cfg, name)
    if value is None:
        raise AttributeError(f"Missing required layout config key: {name!r}.")
    return value


def resolve_evaluation_window_from_config(cfg: Any) -> EvaluationWindow:
    """Build the scoring window from post-config keys.

    ``n_lead_excluded`` and ``n_trail_excluded`` are required (no silent
    lead=1 default). If ``n_inlet_spacer_cells_excluded`` is also set it
    must equal ``n_lead_excluded`` — neither name is preferred silently.
    """
    window = EvaluationWindow(
        int(_require_config_attr(cfg, "n_lead_excluded")),
        int(_require_config_attr(cfg, "n_trail_excluded")),
    )
    if _config_has_layout_value(cfg, "n_inlet_spacer_cells_excluded"):
        n_inlet = cfg.n_inlet_spacer_cells_excluded
        if isinstance(n_inlet, bool) or not isinstance(n_inlet, int):
            raise TypeError(
                "n_inlet_spacer_cells_excluded must be an integer."
            )
        if n_inlet != window.n_lead_excluded:
            raise ValueError(
                "n_inlet_spacer_cells_excluded contradicts n_lead_excluded: "
                f"n_inlet_spacer_cells_excluded={n_inlet!r}, "
                f"n_lead_excluded={window.n_lead_excluded!r}."
            )
    return window


def resolve_scoring_layout_from_config(cfg: Any) -> ScoringLayoutGeometry:
    """Build scoring geometry from asymmetric layout keys on a post config.

    Asymmetric keys ``n_buffer_in``, ``n_active``, ``n_buffer_out``,
    ``cell_length_x_m``, ``buffer_length_in_m``, and ``buffer_length_out_m``
    are required (no silent 5/1 defaults).

    Rule when legacy geometry keys are also present: they must agree with the
    asymmetric layout. Disagreement raises — neither side is preferred silently.
    ``buffer_length_m`` means the inlet-side buffer length
    (``buffer_length_in_m``). ``n_buffer_cells_each_end`` is only
    valid when ``n_buffer_in == n_buffer_out``; a non-None value on an
    asymmetric layout is a contradiction. A ``None`` value is treated as
    absent (used to clear the key via overrides).
    """
    layout = DomainLayout(
        int(_require_config_attr(cfg, "n_buffer_in")),
        int(_require_config_attr(cfg, "n_active")),
        int(_require_config_attr(cfg, "n_buffer_out")),
        float(_require_config_attr(cfg, "cell_length_x_m")),
        float(_require_config_attr(cfg, "buffer_length_in_m")),
        float(_require_config_attr(cfg, "buffer_length_out_m")),
    )
    domain_x_min_m = float(_require_config_attr(cfg, "domain_x_min_m"))
    geometry = scoring_geometry_from_layout(layout, domain_x_min_m)

    if _config_has_layout_value(cfg, "domain_length_m"):
        domain_length_m = float(cfg.domain_length_m)
        if not math.isclose(
            domain_length_m,
            geometry.domain_length_m,
            rel_tol=_LAYOUT_REL_TOL,
            abs_tol=_LAYOUT_ABS_TOL,
        ):
            raise ValueError(
                "domain_length_m contradicts asymmetric layout: "
                f"domain_length_m={domain_length_m!r}, "
                f"layout.total_length_m={geometry.domain_length_m!r} "
                f"(n_buffer_in={layout.n_buffer_in}, n_active={layout.n_active}, "
                f"n_buffer_out={layout.n_buffer_out}, "
                f"cell_length_x_m={layout.cell_length_x_m}, "
                f"buffer_length_in_m={layout.buffer_length_in_m}, "
                f"buffer_length_out_m={layout.buffer_length_out_m})."
            )

    if _config_has_layout_value(cfg, "n_unit_cells"):
        n_unit_cells = int(cfg.n_unit_cells)
        if n_unit_cells != layout.n_total:
            raise ValueError(
                "n_unit_cells contradicts asymmetric layout: "
                f"n_unit_cells={n_unit_cells!r}, "
                f"layout.n_total={layout.n_total!r}."
            )

    if _config_has_layout_value(cfg, "buffer_length_m"):
        buffer_length_m = float(cfg.buffer_length_m)
        expected_inlet_buffer_m = float(layout.buffer_length_in_m)
        if not math.isclose(
            buffer_length_m,
            expected_inlet_buffer_m,
            rel_tol=_LAYOUT_REL_TOL,
            abs_tol=_LAYOUT_ABS_TOL,
        ):
            raise ValueError(
                "buffer_length_m contradicts asymmetric layout inlet buffer: "
                f"buffer_length_m={buffer_length_m!r}, "
                f"expected={expected_inlet_buffer_m!r} "
                f"(buffer_length_in_m)."
            )

    if _config_has_layout_value(cfg, "n_buffer_cells_each_end"):
        n_each = int(cfg.n_buffer_cells_each_end)
        if layout.n_buffer_in != layout.n_buffer_out or n_each != layout.n_buffer_in:
            raise ValueError(
                "n_buffer_cells_each_end contradicts asymmetric layout: "
                f"n_buffer_cells_each_end={n_each!r}, "
                f"n_buffer_in={layout.n_buffer_in}, "
                f"n_buffer_out={layout.n_buffer_out}. "
                "Clear n_buffer_cells_each_end (set to None) for asymmetric "
                "layouts; do not invent a fake each-end count."
            )

    if geometry.spacer_length_m <= 0.0:
        raise ValueError(
            f"spacer_length_m must be > 0, got {geometry.spacer_length_m}."
        )
    return geometry


def unit_cell_plane_name(boundary_index):
    return f"pp_plane_unit_cell_boundary_{boundary_index}"


def unit_cell_pressure_report_name(boundary_index):
    return f"pp_p_unit_cell_boundary_{boundary_index}_avg"


def unit_cell_concentration_report_name(boundary_index):
    return f"pp_salt_mass_fraction_unit_cell_boundary_{boundary_index}_avg"


def unit_cell_mixing_cup_report_name(boundary_index):
    return (
        "pp_salt_mass_fraction_unit_cell_boundary_"
        f"{boundary_index}_massavg"
    )


def unit_cell_plane_area_report_name(boundary_index):
    return f"pp_area_unit_cell_boundary_{boundary_index}"


def unit_cell_areaavg_molar_concentration_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{boundary_index}_areaavg_mol_m3"
    )


def unit_cell_mixing_cup_molar_concentration_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{boundary_index}_massavg_mol_m3"
    )


def unit_cell_mixing_cup_report_spec(boundary_index, field_name="nacl"):
    """Return the Fluent mass-weighted plane concentration report spec."""
    return (
        unit_cell_mixing_cup_report_name(boundary_index),
        "surface-massavg",
        field_name,
    )


from ro.udm_layout import (  # noqa: E402
    FIELD_UDM_MEMBRANE_AREA_ACC as _DEFAULT_UDM_AREA_FIELD,
)


def udm_area_sum_report_spec(field_name=None):
    """Return the Fluent report spec for the unweighted membrane-area UDM sum."""
    if field_name is None:
        field_name = _DEFAULT_UDM_AREA_FIELD
    return ("pp_udm_area_sum", "volume-sum", field_name)


def validate_concentration_thresholds(lower_threshold, upper_threshold):
    """Return validated salt mass-fraction diagnostic thresholds."""
    for name, value in (
        ("lower_threshold", lower_threshold),
        ("upper_threshold", upper_threshold),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be numeric, got {value!r}.")
    if not 0.0 <= lower_threshold < upper_threshold <= 1.0:
        raise ValueError(
            "Concentration thresholds must satisfy "
            "0 <= lower < upper <= 1: "
            f"lower={lower_threshold!r}, upper={upper_threshold!r}."
        )
    return float(lower_threshold), float(upper_threshold)


def fluid_zone_reduction_locations(setup, fluid_zone_names):
    """Resolve fluid-zone names to settings objects accepted by reductions."""
    if not fluid_zone_names:
        raise ValueError("At least one fluid zone is required.")
    fluid_group = setup.cell_zone_conditions.fluid
    locations = []
    for zone_name in fluid_zone_names:
        try:
            locations.append(fluid_group[zone_name])
        except Exception as exc:
            raise ValueError(
                f"Could not resolve fluid zone {zone_name!r} "
                "to a settings object."
            ) from exc
    return locations


def wall_zone_reduction_locations(setup, wall_zone_names):
    """Resolve wall-zone names to settings objects accepted by reductions."""
    if not wall_zone_names:
        raise ValueError("At least one wall zone is required.")
    wall_group = setup.boundary_conditions.wall
    locations = []
    for zone_name in wall_zone_names:
        try:
            locations.append(wall_group[zone_name])
        except Exception as exc:
            raise ValueError(
                f"Could not resolve wall zone {zone_name!r} "
                "to a settings object."
            ) from exc
    return locations


_SURFACE_AREA = "surface-area"
_SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
_SURFACE_MASS_WEIGHTED_AVG = "surface-massavg"
_SURFACE_FACET_MAX = "surface-facetmax"
_SURFACE_FACET_MIN = "surface-facetmin"


def _accum_subphase_seconds(
    bucket: MutableMapping[str, float] | None,
    key: str,
    elapsed_s: float,
) -> None:
    if bucket is not None:
        bucket[key] = bucket.get(key, 0.0) + elapsed_s


# Required in summary_metrics_wide.csv when report extraction succeeds.
CANONICAL_CP_SUMMARY_COLUMNS = (
    "c_b_window_mol_m3",
    "cp_canon_window_avg",
    "cp_canon_window_max",
    "cp_L1_window_avg",
    "cp_L2_window_avg",
    "cp_canon_rescale_delta_max",
    "cp_canon_rescale_delta_status",
    "cp_scalar_rescale_guard_threshold",
)

CP_RESCALE_DELTA_STATUS_EVALUATED = "evaluated"
CP_RESCALE_DELTA_STATUS_NOT_EVALUATED = "not_evaluated"


def require_canonical_cp_summary_columns(wide_record: Mapping[str, Any]) -> None:
    """Raise if a successful extract is missing campaign canonical CP columns.

    ``cp_canon_rescale_delta_max`` may be null when
    ``cp_canon_rescale_delta_status`` is ``not_evaluated`` (spread skipped).
    A missing or empty status is never treated as a passing delta.
    """
    missing = []
    for column in CANONICAL_CP_SUMMARY_COLUMNS:
        if column not in wide_record:
            missing.append(column)
            continue
        if column == "cp_canon_rescale_delta_max":
            continue
        if wide_record[column] in (None, ""):
            missing.append(column)
    status = wide_record.get("cp_canon_rescale_delta_status")
    delta_max = wide_record.get("cp_canon_rescale_delta_max")
    if status == CP_RESCALE_DELTA_STATUS_EVALUATED and delta_max in (None, ""):
        missing.append("cp_canon_rescale_delta_max")
    if status not in (
        CP_RESCALE_DELTA_STATUS_EVALUATED,
        CP_RESCALE_DELTA_STATUS_NOT_EVALUATED,
        None,
        "",
    ):
        raise RuntimeError(
            "Canonical CP summary has unknown cp_canon_rescale_delta_status "
            f"{status!r}; expected "
            f"{CP_RESCALE_DELTA_STATUS_EVALUATED!r} or "
            f"{CP_RESCALE_DELTA_STATUS_NOT_EVALUATED!r}."
        )
    if missing:
        raise RuntimeError(
            "Canonical CP cannot be computed: summary_metrics_wide is missing "
            f"required columns {missing!r}. "
            "cp_inlet_avg (L2) is not a substitute for canonical CP."
        )


def iso_surface_reduction_locations(solver, iso_surface_names):
    """Resolve iso-surface names via ``solver.settings.results.surfaces``.

    Pass the solver session (object with ``.settings.results``), not
    ``solver.settings.setup``. On Fluent 25.1 setup has no ``.results``.
    """
    if not iso_surface_names:
        raise ValueError("At least one iso-surface name is required.")
    try:
        iso_group = solver.settings.results.surfaces.iso_surface
    except AttributeError as exc:
        raise TypeError(
            "iso_surface_reduction_locations expects the solver session "
            "(solver.settings.results.surfaces.iso_surface). "
            "Do not pass solver.settings.setup — setup has no .results "
            "on Fluent 25.1."
        ) from exc
    locations = []
    for surface_name in iso_surface_names:
        try:
            locations.append(iso_group[surface_name])
        except Exception as err:
            raise ValueError(
                f"Could not resolve iso-surface {surface_name!r} "
                "to a settings object."
            ) from err
    return locations


def _find_first_number(obj):
    """Recursively find the first numeric value inside a compute result."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_first_number(value)
            if found is not None:
                return found
    if isinstance(obj, (list, tuple)):
        for value in obj:
            found = _find_first_number(value)
            if found is not None:
                return found
    return None


FLUX_WITHOUT_SOURCES_SUFFIX = "(without-sources)"
FLUX_USER_MASS_SOURCE_SUFFIX = "(User Mass Source)"
FLUX_DECOMPOSITION_ABS_TOL_KG_S = 1.0e-8
FLUX_BOUNDARY_MASSFLOW_REPORTS = frozenset({"pp_m_in", "pp_m_out", "m_in", "m_out"})


class FluxMassflowDecompositionError(ValueError):
    """Raised when a flux-massflow compute payload lacks a valid decomposition."""


def flux_massflow_decomposition_keys(report_name: str) -> tuple[str, str, str]:
    """Return exact Fluent keys for bare, without-sources, and mass-source rows."""
    return (
        report_name,
        f"{report_name}{FLUX_WITHOUT_SOURCES_SUFFIX}",
        f"{report_name}{FLUX_USER_MASS_SOURCE_SUFFIX}",
    )


def _coerce_report_compute_scalar(value: Any) -> float | None:
    """Return the first numeric scalar from a Fluent report compute value."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            coerced = _coerce_report_compute_scalar(item)
            if coerced is not None:
                return coerced
    return None


def _numeric_maps_in_payload(payload: Any) -> list[dict[str, float]]:
    """Collect every dict level whose values coerce to numeric scalars."""
    maps: list[dict[str, float]] = []

    def _visit(obj: Any) -> None:
        if isinstance(obj, dict):
            numeric = {}
            for key, value in obj.items():
                scalar = _coerce_report_compute_scalar(value)
                if scalar is not None:
                    numeric[str(key)] = scalar
            if numeric:
                maps.append(numeric)
            for value in obj.values():
                if not isinstance(value, (int, float)):
                    _visit(value)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _visit(item)

    _visit(payload)
    return maps


def _find_flux_decomposition_map(payload: Any, report_name: str) -> dict[str, float] | None:
    """Return the sibling map containing all three exact flux decomposition keys."""
    bare_key, without_key, mass_source_key = flux_massflow_decomposition_keys(
        report_name
    )
    for numeric in _numeric_maps_in_payload(payload):
        if (
            bare_key in numeric
            and without_key in numeric
            and mass_source_key in numeric
        ):
            return {
                bare_key: numeric[bare_key],
                without_key: numeric[without_key],
                mass_source_key: numeric[mass_source_key],
            }
    return None


def parse_flux_massflow_decomposition(
    payload: Any,
    report_name: str,
    *,
    abs_tol_kg_s: float = FLUX_DECOMPOSITION_ABS_TOL_KG_S,
) -> dict[str, float]:
    """Parse Fluent flux-massflow compute output into boundary/source components.

    Fluent 25.1 decomposes mass-flux reports on zones with volumetric mass
    sources into sibling keys (exact names, same dict level):
      - ``{name}`` — net flux including adjacent-cell source contribution
      - ``{name}(without-sources)`` — pure boundary (face) mass flow
      - ``{name}(User Mass Source)`` — integrated user mass source at the zone
    """
    bare_key, without_key, mass_source_key = flux_massflow_decomposition_keys(
        report_name
    )
    component_map = _find_flux_decomposition_map(payload, report_name)
    if component_map is None:
        found_keys = list_compute_payload_numeric_keys(payload)
        raise FluxMassflowDecompositionError(
            f"Flux report {report_name!r} is missing one or more decomposed keys. "
            f"Required exact keys: {[bare_key, without_key, mass_source_key]!r}. "
            f"Numeric keys found in payload: {found_keys!r}."
        )

    with_sources = component_map[bare_key]
    without_sources = component_map[without_key]
    mass_source = component_map[mass_source_key]

    if (
        math.isclose(with_sources, without_sources, rel_tol=0.0, abs_tol=abs_tol_kg_s)
        and not math.isclose(mass_source, 0.0, rel_tol=0.0, abs_tol=abs_tol_kg_s)
    ):
        raise FluxMassflowDecompositionError(
            f"Flux report {report_name!r} decomposition is degenerate: "
            f"without_sources equals bare ({without_sources:.12e} kg/s) while "
            f"mass_source is non-zero ({mass_source:.12e} kg/s). "
            "The parenthesized keys were not parsed from the payload."
        )

    if not math.isclose(
        with_sources,
        without_sources + mass_source,
        rel_tol=0.0,
        abs_tol=abs_tol_kg_s,
    ):
        residual = with_sources - (without_sources + mass_source)
        raise FluxMassflowDecompositionError(
            f"Flux report {report_name!r} decomposition identity failed: "
            f"bare={with_sources:.12e}, without_sources={without_sources:.12e}, "
            f"mass_source={mass_source:.12e}, residual={residual:.12e} kg/s."
        )

    return {
        "with_sources": with_sources,
        "without_sources": without_sources,
        "mass_source": mass_source,
    }


def compute_flux_massflow_boundary_report(solution, report_name: str):
    """Compute a boundary flux-massflow report; return physical flux + raw payload."""
    result = solution.report_definitions.compute(report_defs=[report_name])
    decomposition = parse_flux_massflow_decomposition(result, report_name)
    without_sources = decomposition["without_sources"]
    return without_sources, result, decomposition


def list_compute_payload_numeric_keys(payload: Any) -> list[str]:
    """Return string keys in a compute payload that map directly to numbers."""
    keys: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    keys.append(str(key))
                else:
                    _walk(value)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _walk(item)

    _walk(payload)
    return keys


def create_x_range_iso_clip(solver, clip_name, surface_names, x_min_m, x_max_m):
    """Create an x-coordinate iso-clip of named surfaces (Fluent 25.1 path).

    Live-verified attributes (ansys-fluent-core 0.38.0):
      clip.field = "x-coordinate"
      clip.surfaces = [...]
      clip.range.minimum / clip.range.maximum
    """
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
    iso_group.create(clip_name)
    clip = iso_group[clip_name]
    clip.field = "x-coordinate"
    clip.surfaces = list(surface_names)
    clip.range.minimum = float(x_min_m)
    clip.range.maximum = float(x_max_m)
    return clip_name


def create_field_iso_clip(
    solver,
    clip_name: str,
    surface_names: list[str],
    field: str,
    range_min: float,
    range_max: float,
) -> str:
    """Create or update a field-value iso-clip (Fluent 25.1 path).

    Reuses ``clip_name`` in place when it already exists (update field /
    surfaces / range) so bisection does not accumulate surfaces.
    """
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        clip = iso_group[clip_name]
    else:
        iso_group.create(clip_name)
        clip = iso_group[clip_name]
    clip.field = field
    clip.surfaces = list(surface_names)
    clip.range.minimum = float(range_min)
    clip.range.maximum = float(range_max)
    return clip_name


def delete_iso_clip(solver, clip_name):
    """Delete an iso-clip surface if it exists."""
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)


def create_or_update_surface_field_report(
    solution,
    report_name,
    report_type,
    field_name,
    surface_names,
):
    """Create/update a surface report definition (shared post path)."""
    if not surface_names:
        raise ValueError(f"Cannot create {report_name}: no surfaces.")
    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")
    if report_name in names:
        rd = group[report_name]
    else:
        rd = group.create(report_name)
    rd.report_type = report_type
    if field_name is not None:
        rd.field = field_name
    rd.surface_names = list(surface_names)
    rd.per_surface = False
    return report_name


def delete_surface_field_report(solution, report_name):
    """Delete a surface report definition if it exists."""
    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")
    if report_name in names:
        group.delete(report_name)


def compute_surface_report_value(solution, report_name):
    """Compute one surface report and return its first numeric value."""
    result = solution.report_definitions.compute(report_defs=[report_name])
    value = _find_first_number(result)
    if value is None:
        raise RuntimeError(
            f"Could not extract numeric value from report {report_name!r}. "
            f"Raw result: {result!r}"
        )
    return value


def evaluation_window_midplane_bulk_concentrations(
    solver,
    solution,
    midplane_surface_names,
    unit_cell_boundary_x_m,
    evaluation_cell_numbers,
    salt_field,
    *,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    salt_is_mass_fraction=True,
):
    """Mid-plane (z = 0.5*(z_min+z_max), channel centre) mixing-cup salt concentration per evaluation cell.

    Uses x-range iso_clip on the mid-plane iso-surface plus surface-area and
    surface-massavg reports (no ``reduction.sum_if``). Mass-weighted average
    is the campaign bulk for conserved species; see metrics_conventions.md.

    Returns (c_b_by_cell, midplane_area_by_cell, c_b_window) in mol/m3.
    Whole-domain mid-plane averages include inlet-buffer regions and must not
    be substituted here.
    """
    if not midplane_surface_names:
        raise ValueError("At least one mid-plane surface name is required.")
    if not evaluation_cell_numbers:
        raise ValueError("At least one evaluation cell is required.")

    c_b_by_cell: dict[int, float] = {}
    midplane_area_by_cell: dict[int, float] = {}

    for cell_number in evaluation_cell_numbers:
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        tag = f"cb_cell_{cell_number}"
        clip_name = f"pp_mid_clip_{tag}"
        report_area = f"pp_mid_area_{tag}"
        report_salt = f"pp_mid_salt_{tag}"
        created_reports: list[str] = []
        create_x_range_iso_clip(
            solver,
            clip_name,
            list(midplane_surface_names),
            x_min_m,
            x_max_m,
        )
        try:
            create_or_update_surface_field_report(
                solution,
                report_area,
                _SURFACE_AREA,
                None,
                [clip_name],
            )
            created_reports.append(report_area)
            area_m2 = float(compute_surface_report_value(solution, report_area))
            if area_m2 <= 0.0:
                raise ValueError(
                    f"Mid-plane segment for evaluation cell {cell_number} has no "
                    f"positive area (x=[{x_min_m!r}, {x_max_m!r} m])."
                )
            create_or_update_surface_field_report(
                solution,
                report_salt,
                _SURFACE_MASS_WEIGHTED_AVG,
                salt_field,
                [clip_name],
            )
            created_reports.append(report_salt)
            salt_avg = float(compute_surface_report_value(solution, report_salt))
            if salt_is_mass_fraction:
                c_b = mass_fraction_to_molar_concentration(
                    salt_avg,
                    density_kg_per_m3,
                    molecular_weight_kg_per_mol,
                )
            else:
                c_b = float(salt_avg)
            if c_b is None or c_b <= 0.0:
                raise ValueError(
                    f"Mid-plane bulk concentration is not positive for cell "
                    f"{cell_number}: {c_b!r}."
                )
            c_b_by_cell[cell_number] = c_b
            midplane_area_by_cell[cell_number] = area_m2
        finally:
            for report_name in created_reports:
                try:
                    delete_surface_field_report(solution, report_name)
                except Exception:
                    pass
            try:
                delete_iso_clip(solver, clip_name)
            except Exception:
                pass

    c_b_window = midplane_window_bulk_aggregate(
        c_b_by_cell,
        midplane_area_by_cell,
        evaluation_cell_numbers,
    )
    return c_b_by_cell, midplane_area_by_cell, c_b_window


def _compute_membrane_segment_via_iso_clip(
    solver,
    solution,
    wall_surface_names,
    x_min_m,
    x_max_m,
    b_perm,
    *,
    tag,
    cm_field="udm-7",
    jw_field="udm-6",
    cp_field="udm-9",
    compute_spread=True,
    warm_start=None,
    subphase_seconds: MutableMapping[str, float] | None = None,
):
    """Area-weighted membrane metrics on one x-clipped wall surface set.

    iso_clip + surface-area / surface-areaavg / surface-facetmax|min on UDM
    fields only. When ``compute_spread`` is True, the rescale-guard spread
    uses:
      - cp_min from area-backed facet minima (cm) + facetmax (Jw)
      - cp_max from 99.9% cm / 0.1% Jw area quantiles (iso_clip bisection)

    Nested field clips reuse one name per quantity and are deleted after each
    area probe (same pattern as the quantile diagnostic) so surfaces do not
    accumulate. ``warm_start`` may supply prior-cell ``cm_q_hi`` / ``jw_q_lo``
    to seed the bisection brackets.
    """
    if not wall_surface_names:
        return None

    clip_name = f"pp_mem_clip_{tag}"
    reports = {
        "area": f"pp_mem_area_{tag}",
        "cm": f"pp_mem_cm_{tag}",
        "jw": f"pp_mem_jw_{tag}",
        "cp": f"pp_mem_cp_{tag}",
        "cp_max": f"pp_mem_cp_max_{tag}",
        "cm_max": f"pp_mem_cm_max_{tag}",
        "cm_min": f"pp_mem_cm_min_{tag}",
        "jw_max": f"pp_mem_jw_max_{tag}",
        "jw_min": f"pp_mem_jw_min_{tag}",
    }
    created_reports: list[str] = []
    created_clips: list[str] = [clip_name]
    n_computes = 0
    t_clip = time.monotonic()
    create_x_range_iso_clip(
        solver,
        clip_name,
        list(wall_surface_names),
        x_min_m,
        x_max_m,
    )
    _accum_subphase_seconds(subphase_seconds, "iso_clip_create", time.monotonic() - t_clip)
    try:
        t_def = time.monotonic()
        create_or_update_surface_field_report(
            solution,
            reports["area"],
            _SURFACE_AREA,
            None,
            [clip_name],
        )
        _accum_subphase_seconds(
            subphase_seconds,
            "surface_report_def_create",
            time.monotonic() - t_def,
        )
        created_reports.append(reports["area"])
        t_compute = time.monotonic()
        area_m2 = float(compute_surface_report_value(solution, reports["area"]))
        _accum_subphase_seconds(
            subphase_seconds,
            "surface_report_compute",
            time.monotonic() - t_compute,
        )
        n_computes += 1
        if area_m2 <= 0.0:
            return None

        def _avg(report_key, field):
            nonlocal n_computes
            t_def = time.monotonic()
            create_or_update_surface_field_report(
                solution,
                reports[report_key],
                _SURFACE_AREA_WEIGHTED_AVG,
                field,
                [clip_name],
            )
            _accum_subphase_seconds(
                subphase_seconds,
                "surface_report_def_create",
                time.monotonic() - t_def,
            )
            created_reports.append(reports[report_key])
            t_compute = time.monotonic()
            value = float(compute_surface_report_value(solution, reports[report_key]))
            _accum_subphase_seconds(
                subphase_seconds,
                "surface_report_compute",
                time.monotonic() - t_compute,
            )
            n_computes += 1
            return value

        def _facet(report_key, report_type, field):
            nonlocal n_computes
            t_def = time.monotonic()
            create_or_update_surface_field_report(
                solution,
                reports[report_key],
                report_type,
                field,
                [clip_name],
            )
            _accum_subphase_seconds(
                subphase_seconds,
                "surface_report_def_create",
                time.monotonic() - t_def,
            )
            created_reports.append(reports[report_key])
            t_compute = time.monotonic()
            value = float(compute_surface_report_value(solution, reports[report_key]))
            _accum_subphase_seconds(
                subphase_seconds,
                "surface_report_compute",
                time.monotonic() - t_compute,
            )
            n_computes += 1
            return value

        def _area_frac_field_below(field: str, threshold: float, sub_tag: str) -> float:
            """Area fraction on the x-clip with field in [0, threshold].

            One reusable clip + area report per ``sub_tag``; deleted after the
            probe so Fluent never accumulates bisection surfaces.
            """
            nonlocal n_computes
            thr = float(threshold)
            if thr < 0.0:
                return 0.0
            child = f"pp_mem_ab_{sub_tag}_{tag}"
            area_name = f"pp_mem_ab_area_{sub_tag}_{tag}"
            t_clip = time.monotonic()
            create_field_iso_clip(
                solver,
                child,
                [clip_name],
                field,
                0.0,
                thr,
            )
            _accum_subphase_seconds(
                subphase_seconds, "iso_clip_create", time.monotonic() - t_clip
            )
            t_def = time.monotonic()
            create_or_update_surface_field_report(
                solution,
                area_name,
                _SURFACE_AREA,
                None,
                [child],
            )
            _accum_subphase_seconds(
                subphase_seconds,
                "surface_report_def_create",
                time.monotonic() - t_def,
            )
            t_compute = time.monotonic()
            try:
                sub_area = float(compute_surface_report_value(solution, area_name))
                n_computes += 1
            except Exception:
                sub_area = 0.0
                n_computes += 1
            finally:
                _accum_subphase_seconds(
                    subphase_seconds,
                    "surface_report_compute",
                    time.monotonic() - t_compute,
                )
            try:
                delete_surface_field_report(solution, area_name)
            except Exception:
                pass
            try:
                delete_iso_clip(solver, child)
            except Exception:
                pass
            return max(0.0, sub_area) / area_m2

        def _area_quantile(
            field: str,
            quantile: float,
            lo_bound: float,
            hi_bound: float,
            label: str,
            seed=None,
        ) -> float:
            lo = float(lo_bound)
            hi = float(hi_bound)
            if hi < lo:
                lo, hi = hi, lo

            def frac(t: float, _f=field, _l=label) -> float:
                return _area_frac_field_below(_f, t, _l)

            # Warm-start: collapse one side of the bracket using the prior cell.
            if seed is not None and math.isfinite(float(seed)):
                s = float(seed)
                if lo < s < hi:
                    fs = frac(s)
                    if fs >= float(quantile):
                        hi = s
                    else:
                        lo = s

            # Expand upper bracket when the high quantile exceeds hi_bound.
            expand = 0
            frac_hi = frac(hi)
            while frac_hi < float(quantile) and expand < 8:
                span = max(hi - lo, abs(hi), 1.0e-30)
                hi = hi + span
                expand += 1
                frac_hi = frac(hi)
            result = bisect_area_fraction_threshold(
                frac,
                target_frac=float(quantile),
                lo=lo,
                hi=hi,
            )
            return result.value

        def _area_backed_min(field: str, facet_min: float, search_upper: float, label: str):
            check_hi = facet_min_check_threshold(facet_min)
            frac_at_check = _area_frac_field_below(field, check_hi, f"{label}_chk")
            used, rejected = resolve_area_backed_minimum(
                facet_min,
                area_frac_at_check=frac_at_check,
                area_frac_below_fn=lambda thr, _f=field, _l=label: (
                    _area_frac_field_below(_f, thr, f"{_l}_bis")
                ),
                search_upper=search_upper,
                reject_frac=FACET_MIN_REJECT_AREA_FRAC,
                target_frac=FACET_MIN_TARGET_AREA_FRAC,
            )
            return used, rejected

        cm_avg = _avg("cm", cm_field)
        jw_avg = _avg("jw", jw_field)
        cp_udm9_avg = _avg("cp", cp_field)
        cp_udm9_max = _facet("cp_max", _SURFACE_FACET_MAX, cp_field)
        cm_max_raw = _facet("cm_max", _SURFACE_FACET_MAX, cm_field)
        cm_min_raw = _facet("cm_min", _SURFACE_FACET_MIN, cm_field)
        jw_max_raw = _facet("jw_max", _SURFACE_FACET_MAX, jw_field)
        jw_min_raw = _facet("jw_min", _SURFACE_FACET_MIN, jw_field)

        cp_perm_avg = film_theory_cp_perm_mol_m3(cm_avg, jw_avg, b_perm)

        if not compute_spread:
            return {
                "area_m2": area_m2,
                "cm_avg": cm_avg,
                "jw_avg": jw_avg,
                "cp_udm9_avg": cp_udm9_avg,
                "cp_perm_avg": cp_perm_avg,
                "cp_perm_min": cp_perm_avg,
                "cp_perm_max": cp_perm_avg,
                "cp_udm9_max": cp_udm9_max,
                "cm_max": cm_max_raw,
                "cm_min_raw": cm_min_raw,
                "cm_min_used": cm_min_raw,
                "jw_min_raw": jw_min_raw,
                "jw_min_used": jw_min_raw,
                "cm_q_lo": None,
                "cm_q_hi": None,
                "jw_q_lo": None,
                "jw_q_hi": None,
                "facet_min_rejected": False,
                "cm_min_rejected": False,
                "jw_min_rejected": False,
                "has_spread": False,
                "fluent_surface_computes": n_computes,
            }

        warm = warm_start or {}
        cm_min_used, cm_min_rejected = _area_backed_min(
            cm_field,
            cm_min_raw,
            max(cm_avg, cm_min_raw),
            "cm",
        )
        jw_min_used, jw_min_rejected = _area_backed_min(
            jw_field,
            jw_min_raw,
            max(jw_avg, jw_min_raw),
            "jw",
        )
        facet_min_rejected = bool(cm_min_rejected or jw_min_rejected)

        # High-end quantiles only (drive cp_max / spread). Low end uses
        # area-backed mins + facetmax Jw.
        cm_q_hi = _area_quantile(
            cm_field,
            CP_SPREAD_AREA_QUANTILE_HI,
            max(cm_avg, cm_min_raw),
            max(cm_max_raw, cm_avg),
            "cmqhi",
            seed=warm.get("cm_q_hi"),
        )
        jw_q_lo = _area_quantile(
            jw_field,
            CP_SPREAD_AREA_QUANTILE_LO,
            jw_min_raw,
            max(jw_avg, jw_min_raw),
            "jwqlo",
            seed=warm.get("jw_q_lo"),
        )

        cp_perm_min = film_theory_cp_perm_mol_m3(cm_min_used, jw_max_raw, b_perm)
        cp_perm_max = film_theory_cp_perm_mol_m3(cm_q_hi, jw_q_lo, b_perm)

        return {
            "area_m2": area_m2,
            "cm_avg": cm_avg,
            "jw_avg": jw_avg,
            "cp_udm9_avg": cp_udm9_avg,
            "cp_perm_avg": cp_perm_avg,
            "cp_perm_min": cp_perm_min,
            "cp_perm_max": cp_perm_max,
            "cp_udm9_max": cp_udm9_max,
            "cm_max": cm_max_raw,
            "cm_min_raw": cm_min_raw,
            "cm_min_used": cm_min_used,
            "jw_min_raw": jw_min_raw,
            "jw_min_used": jw_min_used,
            "cm_q_lo": None,
            "cm_q_hi": cm_q_hi,
            "jw_q_lo": jw_q_lo,
            "jw_q_hi": None,
            "facet_min_rejected": facet_min_rejected,
            "cm_min_rejected": cm_min_rejected,
            "jw_min_rejected": jw_min_rejected,
            "has_spread": True,
            "fluent_surface_computes": n_computes,
        }
    finally:
        for report_name in reversed(created_reports):
            try:
                delete_surface_field_report(solution, report_name)
            except Exception:
                pass
        for name in reversed(created_clips):
            try:
                delete_iso_clip(solver, name)
            except Exception:
                pass


def _segment_metrics_from_reductions(
    segment,
    cell_number,
    c_b_cell_mol_per_m3,
    c0_mol_per_m3,
):
    """Build per-cell CP metrics from iso_clip membrane segment averages."""
    area_m2 = segment["area_m2"]
    cm_avg = segment["cm_avg"]
    jw_avg = segment["jw_avg"]
    cp_udm9_avg = segment["cp_udm9_avg"]
    cp_perm_avg = segment["cp_perm_avg"]

    has_spread = bool(segment.get("has_spread"))
    if has_spread:
        cp_perm_min = segment["cp_perm_min"]
        cp_perm_max = segment["cp_perm_max"]
        if cp_perm_min is None or cp_perm_max is None:
            cp_perm_min = cp_perm_avg
            cp_perm_max = cp_perm_avg
        k_n, delta = canonical_rescale_factor(
            c0_mol_per_m3,
            c_b_cell_mol_per_m3,
            cp_perm_avg,
            cp_perm_min_mol_per_m3=cp_perm_min,
            cp_perm_max_mol_per_m3=cp_perm_max,
        )
    else:
        # Spread not evaluated: still compute k_N from averages; delta is null
        # (not 0.0) so a skipped bound cannot be read as a passing bound.
        k_n, _ = canonical_rescale_factor(
            c0_mol_per_m3,
            c_b_cell_mol_per_m3,
            cp_perm_avg,
        )
        delta = None

    cp_canon = cp_udm9_avg * k_n
    cp_l1 = cp_l1_gu2017(cm_avg, c_b_cell_mol_per_m3)
    cp_l2 = cp_udm9_avg

    cp_canon_max = float(segment["cp_udm9_max"]) * k_n
    cp_l1_max = float(segment["cm_max"]) / c_b_cell_mol_per_m3
    cp_l2_max = float(segment["cp_udm9_max"])

    metrics = {
        f"pp_membrane_area_cell_{cell_number}_m2": area_m2,
        f"pp_cm_mol_m3_cell_{cell_number}": cm_avg,
        f"pp_jw_m_per_s_cell_{cell_number}": jw_avg,
        f"pp_cp_inlet_unit_cell_boundary_{cell_number}": cp_udm9_avg,
        f"pp_cp_perm_mol_m3_cell_{cell_number}": cp_perm_avg,
        f"pp_c_b_midplane_cell_{cell_number}_mol_m3": c_b_cell_mol_per_m3,
        f"pp_cp_canon_cell_{cell_number}": cp_canon,
        f"pp_cp_L1_cell_{cell_number}": cp_l1,
        f"pp_cp_L2_cell_{cell_number}": cp_l2,
        f"pp_cp_canon_rescale_k_cell_{cell_number}": k_n,
        f"pp_cp_canon_rescale_delta_cell_{cell_number}": delta,
        f"pp_cp_canon_max_cell_{cell_number}": cp_canon_max,
        f"pp_cp_L1_max_cell_{cell_number}": cp_l1_max,
        f"pp_cp_L2_max_cell_{cell_number}": cp_l2_max,
        f"cp_facet_min_rejected_cell_{cell_number}": bool(
            segment.get("facet_min_rejected", False)
        ),
        f"cm_min_raw_cell_{cell_number}": segment.get("cm_min_raw"),
        f"cm_min_used_cell_{cell_number}": segment.get("cm_min_used"),
        f"jw_min_raw_cell_{cell_number}": segment.get("jw_min_raw"),
        f"jw_min_used_cell_{cell_number}": segment.get("jw_min_used"),
        f"cm_q_lo_cell_{cell_number}": segment.get("cm_q_lo"),
        f"cm_q_hi_cell_{cell_number}": segment.get("cm_q_hi"),
        f"jw_q_lo_cell_{cell_number}": segment.get("jw_q_lo"),
        f"jw_q_hi_cell_{cell_number}": segment.get("jw_q_hi"),
    }
    return metrics


def _cp_scope_aggregates(
    cell_numbers,
    membrane_area_by_cell,
    cp_avg_by_cell,
    cp_max_by_cell,
    definition_label,
    scope,
):
    """Return window or all-active aggregate keys for one CP definition."""
    avg_key = f"cp_{definition_label}_{scope}_avg"
    max_key = f"cp_{definition_label}_{scope}_max"
    return {
        avg_key: window_area_weighted_average(
            cp_avg_by_cell,
            membrane_area_by_cell,
            cell_numbers,
        ),
        max_key: window_pointwise_max(
            cp_max_by_cell,
            cell_numbers,
        ),
    }


def mass_fraction_to_molar_concentration(
    mass_fraction,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
):
    """Convert salt mass fraction to molar concentration [mol/m3]."""
    if mass_fraction is None:
        return None
    if density_kg_per_m3 <= 0.0 or molecular_weight_kg_per_mol <= 0.0:
        raise ValueError("Density and molecular weight must be positive.")
    return (
        float(mass_fraction)
        * float(density_kg_per_m3)
        / float(molecular_weight_kg_per_mol)
    )


def molar_concentration_to_mass_fraction(
    molar_concentration,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
):
    """Convert molar concentration [mol/m3] to salt mass fraction."""
    if molar_concentration is None:
        return None
    if density_kg_per_m3 <= 0.0 or molecular_weight_kg_per_mol <= 0.0:
        raise ValueError("Density and molecular weight must be positive.")
    return (
        float(molar_concentration)
        * float(molecular_weight_kg_per_mol)
        / float(density_kg_per_m3)
    )


def concentration_metric_unit(metric_name):
    """Return the expected explicit unit for concentration-like metrics."""
    if "cells_above" in metric_name or "cells_below" in metric_name:
        return "cells"
    if "mass_fraction" in metric_name:
        return "mass_fraction"
    if "mol_m3" in metric_name or metric_name in {
        "cm_avg",
        "cm_max",
        "cm_min",
    }:
        return "mol/m3"
    if "cp_" in metric_name:
        return "-"
    return None


def segmented_membrane_cp_metrics(
    solver,
    solution,
    wall_surface_names,
    unit_cell_boundary_x_m,
    spacer_cells,
    mixing_cup_mass_fraction_by_boundary,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    c_inlet_ref_mol_per_m3,
    salt_permeability_m_per_s,
    *,
    evaluation_cell_numbers=None,
    c_b_by_cell_mol_per_m3=None,
    midplane_area_by_cell_m2=None,
    wall_surfaces_by_name=None,
    compute_cp_spread=False,
    subphase_seconds: MutableMapping[str, float] | None = None,
):
    """Compute x-segmented membrane CP metrics (all-active and optional window).

    Uses iso_clip + surface-area / surface-areaavg / facet max|min reports.
    No ``reduction.sum_if``. When ``evaluation_cell_numbers`` and
    ``c_b_by_cell_mol_per_m3`` are supplied, also emits canonical/L1/L2 window
    aggregates. UDM-9 values are average-of-ratios; canonical applies a
    per-cell scalar rescale.

    ``compute_cp_spread`` (default False): when True, evaluation cells run
    facet-min hygiene + quantile bisection so the scalar-rescale delta guard
    can fire. When False, ``k_N`` is still computed from averages;
    ``cp_canon_rescale_delta_max`` is null and status is ``not_evaluated``.
    """
    if not wall_surface_names:
        raise ValueError("At least one membrane wall surface name is required.")
    b_perm = float(salt_permeability_m_per_s)
    c0 = float(c_inlet_ref_mol_per_m3)
    if b_perm <= 0.0 or c0 <= 0.0:
        raise ValueError("Salt permeability and inlet concentration must be positive.")
    want_spread = bool(compute_cp_spread)

    metrics: dict[str, Any] = {}
    segment_cache: dict[int, dict] = {}
    fluent_computes = 0
    eval_set = (
        set(int(c) for c in evaluation_cell_numbers)
        if evaluation_cell_numbers is not None
        else set()
    )

    def _segment_for(cell_number, surfaces, tag_prefix, *, compute_spread, warm_start=None):
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        return _compute_membrane_segment_via_iso_clip(
            solver,
            solution,
            list(surfaces),
            x_min_m,
            x_max_m,
            b_perm,
            tag=f"{tag_prefix}_{cell_number}",
            compute_spread=compute_spread,
            warm_start=warm_start,
            subphase_seconds=subphase_seconds,
        )

    warm_start = None
    for cell_number in spacer_cells:
        need_spread = want_spread and cell_number in eval_set
        segment = _segment_for(
            cell_number,
            wall_surface_names,
            "comb",
            compute_spread=need_spread,
            warm_start=warm_start if need_spread else None,
        )
        if segment is None:
            raise ValueError(
                f"Membrane segment for cell {cell_number} has no positive area."
            )
        fluent_computes += int(segment.get("fluent_surface_computes", 0))
        segment_cache[cell_number] = segment
        if need_spread and segment.get("has_spread"):
            warm_start = {
                "cm_q_hi": segment.get("cm_q_hi"),
                "jw_q_lo": segment.get("jw_q_lo"),
            }
        t_py = time.monotonic()
        area_m2 = segment["area_m2"]
        cm_avg = segment["cm_avg"]
        jw_avg = segment["jw_avg"]
        cp_inlet_avg = segment["cp_udm9_avg"]
        cp_perm_avg = segment["cp_perm_avg"]

        bulk_mol_per_m3 = mass_fraction_to_molar_concentration(
            mixing_cup_mass_fraction_by_boundary.get(cell_number),
            density_kg_per_m3,
            molecular_weight_kg_per_mol,
        )
        cp_bulk = (
            None
            if bulk_mol_per_m3 in (None, 0.0)
            else cm_avg / bulk_mol_per_m3
        )

        metrics.update({
            f"pp_membrane_area_cell_{cell_number}_m2": area_m2,
            f"pp_cm_mol_m3_cell_{cell_number}": cm_avg,
            f"pp_jw_m_per_s_cell_{cell_number}": jw_avg,
            f"pp_cp_inlet_unit_cell_boundary_{cell_number}": cp_inlet_avg,
            f"pp_cp_bulk_unit_cell_boundary_{cell_number}": cp_bulk,
            f"pp_cp_perm_mol_m3_cell_{cell_number}": cp_perm_avg,
        })
        _accum_subphase_seconds(
            subphase_seconds, "python_aggregate", time.monotonic() - t_py
        )

    if evaluation_cell_numbers is not None:
        if c_b_by_cell_mol_per_m3 is None:
            raise ValueError(
                "c_b_by_cell_mol_per_m3 is required when evaluation_cell_numbers "
                "is set."
            )
        if midplane_area_by_cell_m2 is None:
            raise ValueError(
                "midplane_area_by_cell_m2 is required when evaluation_cell_numbers "
                "is set."
            )
        missing_cells = [
            cell_number
            for cell_number in evaluation_cell_numbers
            if cell_number not in c_b_by_cell_mol_per_m3
        ]
        if missing_cells:
            raise ValueError(
                f"c_b missing for evaluation cells: {missing_cells!r}."
            )

        canon_avg: dict[int, float] = {}
        canon_max: dict[int, float] = {}
        l1_avg: dict[int, float] = {}
        l1_max: dict[int, float] = {}
        l2_avg: dict[int, float] = {}
        l2_max: dict[int, float] = {}
        membrane_area: dict[int, float] = {}
        delta_values: dict[int, Optional[float]] = {}

        for cell_number in evaluation_cell_numbers:
            cached = segment_cache.get(cell_number)
            need_recompute = cached is None or (
                want_spread and not cached.get("has_spread")
            )
            if need_recompute:
                segment = _segment_for(
                    cell_number,
                    wall_surface_names,
                    "comb",
                    compute_spread=want_spread,
                    warm_start=warm_start if want_spread else None,
                )
                if segment is None:
                    raise ValueError(
                        f"Membrane segment for evaluation cell {cell_number} "
                        "has no positive area."
                    )
                fluent_computes += int(segment.get("fluent_surface_computes", 0))
                segment_cache[cell_number] = segment
                if want_spread and segment.get("has_spread"):
                    warm_start = {
                        "cm_q_hi": segment.get("cm_q_hi"),
                        "jw_q_lo": segment.get("jw_q_lo"),
                    }
            t_py = time.monotonic()
            segment = segment_cache[cell_number]
            c_b_cell = c_b_by_cell_mol_per_m3[cell_number]
            cell_metrics = _segment_metrics_from_reductions(
                segment,
                cell_number,
                c_b_cell,
                c0,
            )
            metrics.update(cell_metrics)
            membrane_area[cell_number] = segment["area_m2"]
            canon_avg[cell_number] = cell_metrics[
                f"pp_cp_canon_cell_{cell_number}"
            ]
            canon_max[cell_number] = cell_metrics[
                f"pp_cp_canon_max_cell_{cell_number}"
            ]
            l1_avg[cell_number] = cell_metrics[f"pp_cp_L1_cell_{cell_number}"]
            l1_max[cell_number] = cell_metrics[f"pp_cp_L1_max_cell_{cell_number}"]
            l2_avg[cell_number] = cell_metrics[f"pp_cp_L2_cell_{cell_number}"]
            l2_max[cell_number] = cell_metrics[f"pp_cp_L2_max_cell_{cell_number}"]
            delta_values[cell_number] = cell_metrics[
                f"pp_cp_canon_rescale_delta_cell_{cell_number}"
            ]
            _accum_subphase_seconds(
                subphase_seconds, "python_aggregate", time.monotonic() - t_py
            )

        t_py = time.monotonic()
        metrics["c_b_window_mol_m3"] = midplane_window_bulk_aggregate(
            c_b_by_cell_mol_per_m3,
            midplane_area_by_cell_m2 or {},
            evaluation_cell_numbers,
        )
        metrics["c_inlet_ref_mol_m3"] = c0
        metrics["cp_scalar_rescale_guard_threshold"] = (
            CP_SCALAR_RESCALE_GUARD_THRESHOLD
        )
        metrics["compute_cp_spread"] = want_spread
        if want_spread:
            metrics["cp_canon_rescale_delta_max"] = max(
                float(v) for v in delta_values.values()
            )
            metrics["cp_canon_rescale_delta_status"] = (
                CP_RESCALE_DELTA_STATUS_EVALUATED
            )
        else:
            metrics["cp_canon_rescale_delta_max"] = None
            metrics["cp_canon_rescale_delta_status"] = (
                CP_RESCALE_DELTA_STATUS_NOT_EVALUATED
            )

        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                canon_avg,
                canon_max,
                "canon",
                "window",
            )
        )
        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                l1_avg,
                l1_max,
                "L1",
                "window",
            )
        )
        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                l2_avg,
                l2_max,
                "L2",
                "window",
            )
        )
        _accum_subphase_seconds(
            subphase_seconds, "python_aggregate", time.monotonic() - t_py
        )

        spacer_with_c_b = [
            cell_number
            for cell_number in spacer_cells
            if cell_number in c_b_by_cell_mol_per_m3
        ]
        if spacer_with_c_b:
            t_py = time.monotonic()
            all_canon_avg: dict[int, float] = {}
            all_canon_max: dict[int, float] = {}
            all_l1_avg: dict[int, float] = {}
            all_l1_max: dict[int, float] = {}
            all_l2_avg: dict[int, float] = {}
            all_l2_max: dict[int, float] = {}
            all_membrane_area: dict[int, float] = {}
            for cell_number in spacer_with_c_b:
                if cell_number not in segment_cache:
                    continue
                segment = segment_cache[cell_number]
                c_b_cell = c_b_by_cell_mol_per_m3[cell_number]
                cell_metrics = _segment_metrics_from_reductions(
                    segment,
                    cell_number,
                    c_b_cell,
                    c0,
                )
                all_membrane_area[cell_number] = segment["area_m2"]
                all_canon_avg[cell_number] = cell_metrics[
                    f"pp_cp_canon_cell_{cell_number}"
                ]
                all_canon_max[cell_number] = cell_metrics[
                    f"pp_cp_canon_max_cell_{cell_number}"
                ]
                all_l1_avg[cell_number] = cell_metrics[f"pp_cp_L1_cell_{cell_number}"]
                all_l1_max[cell_number] = cell_metrics[f"pp_cp_L1_max_cell_{cell_number}"]
                all_l2_avg[cell_number] = cell_metrics[f"pp_cp_L2_cell_{cell_number}"]
                all_l2_max[cell_number] = cell_metrics[f"pp_cp_L2_max_cell_{cell_number}"]
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_canon_avg,
                    all_canon_max,
                    "canon",
                    "all_active",
                )
            )
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_l1_avg,
                    all_l1_max,
                    "L1",
                    "all_active",
                )
            )
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_l2_avg,
                    all_l2_max,
                    "L2",
                    "all_active",
                )
            )
            _accum_subphase_seconds(
                subphase_seconds, "python_aggregate", time.monotonic() - t_py
            )

        if wall_surfaces_by_name:
            # Per-wall breakdown reuses the combined-membrane k_N. Independent
            # top/bottom quantile bisection is not needed for the guard and
            # previously tripled the Fluent surface traffic (w_lower / w_upper).
            for wall_name, wall_names in wall_surfaces_by_name.items():
                suffix = _wall_metric_suffix(wall_name)
                per_wall_canon_avg: dict[int, float] = {}
                per_wall_canon_max: dict[int, float] = {}
                per_wall_area: dict[int, float] = {}
                for cell_number in evaluation_cell_numbers:
                    segment = _segment_for(
                        cell_number,
                        wall_names,
                        f"w_{suffix}",
                        compute_spread=False,
                    )
                    if segment is None:
                        raise ValueError(
                            f"Membrane segment for {wall_name} cell "
                            f"{cell_number} has no positive area."
                        )
                    fluent_computes += int(segment.get("fluent_surface_computes", 0))
                    k_n = float(
                        metrics[f"pp_cp_canon_rescale_k_cell_{cell_number}"]
                    )
                    per_wall_area[cell_number] = segment["area_m2"]
                    per_wall_canon_avg[cell_number] = (
                        float(segment["cp_udm9_avg"]) * k_n
                    )
                    per_wall_canon_max[cell_number] = (
                        float(segment["cp_udm9_max"]) * k_n
                    )
                t_py = time.monotonic()
                wall_agg = _cp_scope_aggregates(
                    evaluation_cell_numbers,
                    per_wall_area,
                    per_wall_canon_avg,
                    per_wall_canon_max,
                    f"canon_{suffix}",
                    "window",
                )
                metrics.update(wall_agg)
                _accum_subphase_seconds(
                    subphase_seconds, "python_aggregate", time.monotonic() - t_py
                )

    metrics["cp_membrane_segment_fluent_computes"] = fluent_computes
    return metrics



def _wall_metric_suffix(wall_zone_name: str) -> str:
    lowered = wall_zone_name.lower()
    if "bottom" in lowered:
        return "lower"
    if "top" in lowered:
        return "upper"
    return wall_zone_name.replace("wall_", "")


def exception_details(exc):
    """Return stable exception fields for JSON and CSV diagnostics."""
    error_type = type(exc).__name__
    message = str(exc)
    return {
        "type": error_type,
        "message": message,
        "combined": f"{error_type}: {message}",
    }


def summary_rows_to_wide_record(summary_rows):
    """Build one wide record without dropping metrics whose values are missing."""
    record = {}
    for row in summary_rows:
        metric = row["metric"]
        if metric in record:
            raise ValueError(f"Duplicate summary metric: {metric!r}.")
        record[metric] = row.get("value")
    return record


def concentration_range_diagnostics(
    reduction,
    fluid_zone_locations,
    species_name,
    lower_threshold,
    upper_threshold,
):
    """Compute conditional cell counts and extrema for one species mass fraction."""
    if not fluid_zone_locations:
        raise ValueError("At least one fluid zone is required.")
    if any(isinstance(location, str) for location in fluid_zone_locations):
        raise TypeError(
            "Reduction locations must be Fluent settings objects, not names."
        )
    if not isinstance(species_name, str) or not species_name.strip():
        raise ValueError("species_name must be a non-empty string.")

    lower_threshold, upper_threshold = validate_concentration_thresholds(
        lower_threshold,
        upper_threshold,
    )
    expression = f'MassFraction(species="{species_name}")'
    locations = list(fluid_zone_locations)

    return {
        "pp_salt_mass_fraction_cells_above_threshold": reduction.count_if(
            condition=f"{expression} > {upper_threshold!r}",
            locations=locations,
        ),
        "pp_salt_mass_fraction_cells_below_threshold": reduction.count_if(
            condition=f"{expression} < {lower_threshold!r}",
            locations=locations,
        ),
        "pp_salt_mass_fraction_max": reduction.maximum(
            expression=expression,
            locations=locations,
        ),
        "pp_salt_mass_fraction_min": reduction.minimum(
            expression=expression,
            locations=locations,
        ),
    }


def derive_spacer_cell_metrics(
    computed_values,
    n_unit_cells,
    n_buffer_cells_each_end,
):
    """Derive pressure drop and salt-mass-fraction rise for each spacer cell."""
    derived = {}
    for cell_number in spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    ):
        upstream_index = cell_number - 1
        downstream_index = cell_number
        p_upstream = computed_values.get(
            unit_cell_pressure_report_name(upstream_index)
        )
        p_downstream = computed_values.get(
            unit_cell_pressure_report_name(downstream_index)
        )
        c_upstream = computed_values.get(
            unit_cell_concentration_report_name(upstream_index)
        )
        c_downstream = computed_values.get(
            unit_cell_concentration_report_name(downstream_index)
        )

        derived[f"pp_pressure_drop_cell_{cell_number}"] = (
            None
            if p_upstream is None or p_downstream is None
            else p_upstream - p_downstream
        )
        derived[f"pp_salt_mass_fraction_rise_cell_{cell_number}"] = (
            None
            if c_upstream is None or c_downstream is None
            else c_downstream - c_upstream
        )

    return derived


def derive_spacer_cell_metrics_for_layout(
    computed_values: Mapping[str, Any],
    layout: DomainLayout,
) -> dict[str, Optional[float]]:
    """Asymmetric DomainLayout variant of :func:`derive_spacer_cell_metrics`."""
    derived: dict[str, Optional[float]] = {}
    for cell_number in layout.active_cell_numbers():
        upstream_index = cell_number - 1
        downstream_index = cell_number
        p_upstream = computed_values.get(
            unit_cell_pressure_report_name(upstream_index)
        )
        p_downstream = computed_values.get(
            unit_cell_pressure_report_name(downstream_index)
        )
        c_upstream = computed_values.get(
            unit_cell_concentration_report_name(upstream_index)
        )
        c_downstream = computed_values.get(
            unit_cell_concentration_report_name(downstream_index)
        )

        derived[f"pp_pressure_drop_cell_{cell_number}"] = (
            None
            if p_upstream is None or p_downstream is None
            else p_upstream - p_downstream
        )
        derived[f"pp_salt_mass_fraction_rise_cell_{cell_number}"] = (
            None
            if c_upstream is None or c_downstream is None
            else c_downstream - c_upstream
        )

    return derived


def derive_periodic_spacer_pressure_metrics(
    unit_cell_metrics,
    domain_length_m,
    n_unit_cells,
    n_buffer_cells_each_end,
    n_inlet_spacer_cells_excluded,
):
    """Derive periodic-cell pressure gradient and entrance contamination."""
    if (
        isinstance(n_inlet_spacer_cells_excluded, bool)
        or not isinstance(n_inlet_spacer_cells_excluded, int)
    ):
        raise TypeError(
            "n_inlet_spacer_cells_excluded must be an integer."
        )
    if n_inlet_spacer_cells_excluded < 0:
        raise ValueError(
            "n_inlet_spacer_cells_excluded must be non-negative."
        )

    all_spacer_cells = spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    )
    periodic_cells = all_spacer_cells[n_inlet_spacer_cells_excluded:]
    if not periodic_cells:
        raise ValueError(
            "At least one spacer cell must remain for the periodic average."
        )

    periodic_pressure_drops = [
        unit_cell_metrics.get(f"pp_pressure_drop_cell_{cell_number}")
        for cell_number in periodic_cells
    ]
    if any(value is None for value in periodic_pressure_drops):
        periodic_per_m = None
    else:
        cell_length_m = float(domain_length_m) / n_unit_cells
        periodic_length_m = len(periodic_cells) * cell_length_m
        periodic_per_m = sum(periodic_pressure_drops) / periodic_length_m

    cell2_drop = unit_cell_metrics.get("pp_pressure_drop_cell_2")
    cell3_drop = unit_cell_metrics.get("pp_pressure_drop_cell_3")
    cell2_over_cell3 = (
        None
        if cell2_drop is None or cell3_drop in (None, 0.0)
        else cell2_drop / cell3_drop
    )

    return {
        "pp_pressure_drop_periodic_per_m": periodic_per_m,
        "pp_pressure_drop_cell2_over_cell3": cell2_over_cell3,
    }


def derive_periodic_spacer_pressure_metrics_for_layout(
    unit_cell_metrics: Mapping[str, Any],
    layout: DomainLayout,
    evaluation_window: EvaluationWindow,
) -> dict[str, Optional[float]]:
    """Asymmetric DomainLayout variant of periodic spacer pressure metrics."""
    periodic_cells = evaluation_window.evaluation_cell_numbers(layout)
    if not periodic_cells:
        raise ValueError(
            "At least one spacer cell must remain for the periodic average."
        )

    periodic_pressure_drops = [
        unit_cell_metrics.get(f"pp_pressure_drop_cell_{cell_number}")
        for cell_number in periodic_cells
    ]
    if any(value is None for value in periodic_pressure_drops):
        periodic_per_m = None
    else:
        cell_length_m = float(layout.cell_length_x_m)
        periodic_length_m = len(periodic_cells) * cell_length_m
        periodic_per_m = sum(periodic_pressure_drops) / periodic_length_m

    # Intentionally fixed to cells 2 and 3 (not layout-relative).
    cell2_drop = unit_cell_metrics.get("pp_pressure_drop_cell_2")
    cell3_drop = unit_cell_metrics.get("pp_pressure_drop_cell_3")
    cell2_over_cell3 = (
        None
        if cell2_drop is None or cell3_drop in (None, 0.0)
        else cell2_drop / cell3_drop
    )

    return {
        "pp_pressure_drop_periodic_per_m": periodic_per_m,
        "pp_pressure_drop_cell2_over_cell3": cell2_over_cell3,
    }
