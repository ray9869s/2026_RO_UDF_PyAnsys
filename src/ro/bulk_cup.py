"""Benchmark bulk concentration c_b from x-normal mixing cups (2026-11-22).

The mean-first benchmark CP (``cp_meanfirst_live_window_avg`` and the
all-face ``cp_meanfirst_window_avg``) divides by ``c_b - c_p``. Before
2026-11-22 ``c_b`` was the surface mass-weighted average on the z-normal
channel mid-plane. That is not a mixing cup: it samples the centre of the
channel, so at low velocity, when salt piles up in the near-membrane
layers, it sits near the centre-line value and below the true cup
(u0p05_p6M P_p80_h15: cells 7-8 mid-plane 598.2/598.4 vs cup 601.5/602.2
mol/m3, 0.56-0.63 %).

``c_b`` for the benchmark is now the window mixing cup:

* per evaluation cell N: the mean of the molar mixing-cup concentrations on
  its two x-normal unit-cell boundaries (N-1 and N), each the Fluent
  ``surface-massavg`` of the salt mass fraction on that plane converted
  with the extract's rho and MW;
* window: the same cell weighting as the mid-plane value (mid-plane area of
  each cell).

The mid-plane value stays as a diagnostic (``c_b_midplane_window_mol_m3``)
and the relative difference is recorded; a difference above
``CUP_MIDPLANE_REL_TOL`` is a warning, not an extract failure.

``recompute_meanfirst_cup`` rebuilds both the mid-plane and the cup value
from saved summary columns for a leaf extracted before the change. Nothing
here launches Fluent.
"""

from __future__ import annotations

import math
import re

CUP_MIDPLANE_REL_TOL = 0.005
C_B_BENCHMARK_DEFINITION = "boundary_mixing_cup"
# Equal cell weights stand in for the unsaved mid-plane areas only when an
# equal-weight mean of the saved per-cell mid-plane c_b reproduces the saved
# window value to this relative tolerance.
EQUAL_WEIGHT_REPRODUCTION_REL_TOL = 1.0e-6
# The saved inputs must reproduce the saved mid-plane CP to this tolerance.
CP_REPRODUCTION_ABS_TOL = 1.0e-6

C_B_CUP_WINDOW = "c_b_cup_window_mol_m3"
C_B_MIDPLANE_WINDOW = "c_b_midplane_window_mol_m3"
C_B_REL_DIFF = "c_b_midplane_minus_cup_rel"
C_B_MAX_CELL_REL_DIFF = "c_b_midplane_cup_max_cell_abs_rel"
C_B_CUP_STATUS = "c_b_cup_midplane_status"
C_B_DEFINITION = "c_b_benchmark_definition"
MEANFIRST_LIVE_THRESHOLD = "cp_meanfirst_live_exclusion_threshold_mol_m3"
MEANFIRST_LIVE_CM = "cm_area_mean_window_live_meanfirst"
MEANFIRST_LIVE_MIDPLANE = "cp_meanfirst_live_window_avg_midplane"
MEANFIRST_ALL_MIDPLANE = "cp_meanfirst_window_avg_midplane"

# Saved by a cup-based extract with a ``<key>_midplane`` companion.
MEANFIRST_KEYS = ("cp_meanfirst_live_window_avg", "cp_meanfirst_window_avg")

SUMMARY_METRICS = (
    C_B_CUP_WINDOW,
    C_B_MIDPLANE_WINDOW,
    C_B_REL_DIFF,
    C_B_MAX_CELL_REL_DIFF,
    C_B_CUP_STATUS,
    C_B_DEFINITION,
    MEANFIRST_LIVE_THRESHOLD,
    MEANFIRST_LIVE_CM,
    MEANFIRST_LIVE_MIDPLANE,
    MEANFIRST_ALL_MIDPLANE,
)


def cup_cell_name(cell_number):
    return f"pp_c_b_cup_cell_{int(cell_number)}_mol_m3"


def midplane_area_cell_name(cell_number):
    return f"pp_midplane_area_cell_{int(cell_number)}_m2"


def midplane_cell_name(cell_number):
    return f"pp_c_b_midplane_cell_{int(cell_number)}_mol_m3"


def boundary_cup_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{int(boundary_index)}_massavg_mol_m3"
    )


class CupError(ValueError):
    pass


def _positive(value, label):
    if value is None or isinstance(value, bool):
        raise CupError(f"{label} is missing.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise CupError(f"{label} must be finite and positive, got {value!r}.")
    return number


def cup_by_cell(cup_mol_by_boundary, cell_numbers):
    """Per cell: mean of the molar mixing cups on boundaries N-1 and N."""
    if not cell_numbers:
        raise CupError("cell_numbers must be non-empty.")
    cups = {}
    for cell in cell_numbers:
        left = _positive(
            cup_mol_by_boundary.get(cell - 1), f"mixing cup on boundary {cell - 1}"
        )
        right = _positive(
            cup_mol_by_boundary.get(cell), f"mixing cup on boundary {cell}"
        )
        cups[cell] = 0.5 * (left + right)
    return cups


def weighted_window(values_by_cell, weights_by_cell, cell_numbers):
    total = weighted = 0.0
    for cell in cell_numbers:
        weight = _positive(weights_by_cell.get(cell), f"weight of cell {cell}")
        value = _positive(values_by_cell.get(cell), f"value of cell {cell}")
        total += weight
        weighted += weight * value
    return weighted / total


def cup_midplane_diagnostic(
    midplane_by_cell,
    cups_by_cell,
    cell_numbers,
    *,
    c_b_midplane_window,
    c_b_cup_window,
    rel_tol=CUP_MIDPLANE_REL_TOL,
):
    """Summary columns comparing the mid-plane and cup c_b. Never raises on size."""
    worst = 0.0
    over = []
    for cell in cell_numbers:
        cup = _positive(cups_by_cell.get(cell), f"cup c_b of cell {cell}")
        mid = _positive(midplane_by_cell.get(cell), f"mid-plane c_b of cell {cell}")
        rel = abs(mid - cup) / cup
        worst = max(worst, rel)
        if rel > rel_tol:
            over.append(f"cell {cell}: mid-plane {mid:.6g} vs cup {cup:.6g} ({rel:.3%})")
    cup_w = _positive(c_b_cup_window, "c_b cup window")
    mid_w = _positive(c_b_midplane_window, "c_b mid-plane window")
    if over:
        status = (
            f"warn: mid-plane differs from the cup by more than {rel_tol:.2%}: "
            + "; ".join(over)
        )
    else:
        status = f"ok: max cell |mid-plane - cup|/cup {worst:.3%} <= {rel_tol:.2%}"
    return {
        C_B_CUP_WINDOW: cup_w,
        C_B_MIDPLANE_WINDOW: mid_w,
        C_B_REL_DIFF: (mid_w - cup_w) / cup_w,
        C_B_MAX_CELL_REL_DIFF: worst,
        C_B_CUP_STATUS: status,
        C_B_DEFINITION: C_B_BENCHMARK_DEFINITION,
    }


def per_cell_rows(cups_by_cell, midplane_area_by_cell, cell_numbers):
    rows = []
    for cell in cell_numbers:
        rows.append({"metric": cup_cell_name(cell), "value": cups_by_cell[cell], "unit": "mol/m3"})
        area = midplane_area_by_cell.get(cell)
        rows.append({
            "metric": midplane_area_cell_name(cell),
            "value": None if area is None else float(area),
            "unit": "m2",
        })
    return rows


# ---------------------------------------------------------------------------
# Recompute from saved columns (no Fluent)
# ---------------------------------------------------------------------------

_MIDPLANE_CELL_RE = re.compile(r"^pp_c_b_midplane_cell_(\d+)_mol_m3$")


def _float(wide, key):
    raw = wide.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if not text or text.upper() == "MISSING":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def evaluation_cells_from_mesh_manifest(mesh_manifest):
    """Global evaluation-cell numbers from a mesh manifest, or None."""
    try:
        n_in = int(mesh_manifest["n_buffer_in"])
        n_active = int(mesh_manifest["n_active_cells"])
        lead = int(mesh_manifest["n_lead_excluded"])
        trail = int(mesh_manifest["n_trail_excluded"])
    except (KeyError, TypeError, ValueError):
        return None
    if lead + trail >= n_active:
        return None
    return [n_in + local for local in range(lead + 1, n_active - trail + 1)]


def _midplane_cells(wide):
    return sorted(
        int(match.group(1))
        for key in wide
        if (match := _MIDPLANE_CELL_RE.match(str(key))) and _float(wide, key) is not None
    )


def _ratio(cm, cp_ref, c_b):
    denominator = c_b - cp_ref
    if denominator <= 0.0:
        raise CupError(f"c_b - cp_ref must be positive (c_b={c_b!r}, cp_ref={cp_ref!r}).")
    return (cm - cp_ref) / denominator


def window_cup_from_columns(wide, evaluation_cells=None):
    """``(c_b_cup_window, c_b_midplane_window, weighting)`` from saved columns.

    Weights: saved per-cell mid-plane areas when present; otherwise equal
    weights, accepted only when they reproduce the saved mid-plane window.
    Raises ``CupError`` with the reason when a value cannot be built.
    """
    cells = list(evaluation_cells) if evaluation_cells else _midplane_cells(wide)
    if not cells:
        raise CupError("no evaluation cells (no mesh manifest, no pp_c_b_midplane_cell_* columns)")
    saved_mid = _float(wide, C_B_MIDPLANE_WINDOW)
    if saved_mid is None:
        saved_mid = _float(wide, "c_b_window_mol_m3")
    if saved_mid is None:
        raise CupError("no c_b_window_mol_m3")
    mids = {}
    for cell in cells:
        value = _float(wide, midplane_cell_name(cell))
        if value is None:
            raise CupError(f"no {midplane_cell_name(cell)}")
        mids[cell] = value
    boundaries = {}
    for cell in cells:
        for index in (cell - 1, cell):
            value = _float(wide, boundary_cup_name(index))
            if value is None:
                raise CupError(f"no {boundary_cup_name(index)}")
            boundaries[index] = value
    cups = cup_by_cell(boundaries, cells)
    areas = {cell: _float(wide, midplane_area_cell_name(cell)) for cell in cells}
    if all(area is not None and area > 0.0 for area in areas.values()):
        weights, weighting = areas, "midplane_area"
    else:
        weights = {cell: 1.0 for cell in cells}
        weighting = "equal"
    rebuilt = weighted_window(mids, weights, cells)
    rel = abs(rebuilt - saved_mid) / saved_mid
    if rel > EQUAL_WEIGHT_REPRODUCTION_REL_TOL:
        raise CupError(
            f"{weighting} cell weights over cells {cells[0]}-{cells[-1]} give mid-plane "
            f"{rebuilt:.8g}, saved c_b_window {saved_mid:.8g} (rel {rel:.2e} > "
            f"{EQUAL_WEIGHT_REPRODUCTION_REL_TOL:g})"
        )
    return weighted_window(cups, weights, cells), saved_mid, weighting


def recompute_meanfirst_cup(
    wide,
    *,
    cm_key,
    cp_ref_key,
    saved_cp_key,
    live,
    evaluation_cells=None,
):
    """Mid-plane- and cup-based mean-first CP from one leaf's saved columns.

    Returns ``{"cp_midplane", "cp_cup", "c_b_cup", "c_b_midplane",
    "status"}``; the CP values are None with the reason in ``status`` when
    an input is missing or the saved inputs do not reproduce the saved CP.

    A leaf extracted after the change (``c_b_cup_window_mol_m3`` present)
    already stores both: ``saved_cp_key`` is the cup value and
    ``<saved_cp_key>_midplane`` the mid-plane value.

    ``live``: the live face set is ``c_p < min(c_0, c_b)``. It is the same
    for both c_b only when ``min(c_0, c_b)`` is; otherwise the saved
    live means do not apply to the cup value and it is blank.
    """
    out = {"cp_midplane": None, "cp_cup": None, "c_b_cup": None, "c_b_midplane": None}
    if _float(wide, C_B_CUP_WINDOW) is not None and saved_cp_key in MEANFIRST_KEYS:
        out["c_b_cup"] = _float(wide, C_B_CUP_WINDOW)
        out["c_b_midplane"] = _float(wide, C_B_MIDPLANE_WINDOW)
        out["cp_cup"] = _float(wide, saved_cp_key)
        out["cp_midplane"] = _float(wide, f"{saved_cp_key}_midplane")
        missing = [
            name for name, value in (
                (saved_cp_key, out["cp_cup"]), (f"{saved_cp_key}_midplane", out["cp_midplane"]),
            ) if value is None
        ]
        out["status"] = (
            "ok: cup-based extract" if not missing else "cup-based extract; blank: no " + ", ".join(missing)
        )
        return out
    cm = _float(wide, cm_key)
    cp_ref = _float(wide, cp_ref_key)
    saved = _float(wide, saved_cp_key)
    missing = [k for k, v in ((cm_key, cm), (cp_ref_key, cp_ref), (saved_cp_key, saved)) if v is None]
    if missing:
        out["status"] = "blank: no " + ", ".join(missing)
        return out
    try:
        cup, mid, weighting = window_cup_from_columns(wide, evaluation_cells)
    except CupError as exc:
        out["status"] = f"blank: {exc}"
        return out
    out["c_b_cup"], out["c_b_midplane"] = cup, mid
    try:
        rebuilt = _ratio(cm, cp_ref, mid)
    except CupError as exc:
        out["status"] = f"blank: {exc}"
        return out
    if abs(rebuilt - saved) > CP_REPRODUCTION_ABS_TOL:
        out["status"] = (
            f"blank: {cm_key}/{cp_ref_key} with the mid-plane c_b give {rebuilt:.8g}, "
            f"saved {saved_cp_key} {saved:.8g}"
        )
        return out
    out["cp_midplane"] = saved
    if live:
        c_0 = _float(wide, "c_inlet_ref_mol_m3")
        if c_0 is None:
            out["status"] = "blank: no c_inlet_ref_mol_m3 (live face set unknown)"
            return out
        if min(c_0, mid) != min(c_0, cup):
            out["status"] = (
                f"blank: live face set changes with c_b (min(c_0, c_b) "
                f"{min(c_0, mid):.6g} -> {min(c_0, cup):.6g}); re-extract"
            )
            return out
    try:
        out["cp_cup"] = _ratio(cm, cp_ref, cup)
    except CupError as exc:
        out["status"] = f"blank: {exc}"
        return out
    out["status"] = f"ok: recomputed from saved columns ({weighting} cell weights)"
    return out


# ---------------------------------------------------------------------------
# More c_b definitions, stored so the paper can choose (2026-11-22, round 2)
# ---------------------------------------------------------------------------
#
# cup_massavg  x-normal boundary surface-massavg (weight |rho u.n|; reverse
#              flow counted positive). The benchmark above.
# cup_net      x-normal boundary net-flux cup: Fluent "Flow Rate" of the salt
#              mass fraction (integral of Y rho u.n dA, signed) over "Mass
#              Flow Rate" (integral of rho u.n dA). Reverse flow cancels;
#              convective flux only (axial diffusion across the plane is not
#              in either integral).
# midplane     z-normal channel mid-plane surface-massavg (c_b_window_mol_m3).
# inlet        the inlet concentration c_0 (c_inlet_ref_mol_m3).

SURFACE_FLOW_RATE = "surface-flowrate"
SURFACE_MASS_FLOW_RATE = "surface-massflowrate"

DEFINITION_CUP_MASSAVG = "cup_massavg"
DEFINITION_CUP_NET = "cup_net"
DEFINITION_MIDPLANE = "midplane"
DEFINITION_INLET = "inlet_c0"

C_B_CUPNET_WINDOW = "c_b_cupnet_window_mol_m3"
C_B_CUPNET_REL_DIFF = "c_b_cupnet_minus_cup_rel"
C_B_CUPNET_MAX_CELL_REL_DIFF = "c_b_cupnet_cup_max_cell_abs_rel"
C_B_CUPNET_STATUS = "c_b_cupnet_status"
MEANFIRST_LIVE_CUPNET = "cp_meanfirst_live_window_avg_cupnet"
MEANFIRST_ALL_CUPNET = "cp_meanfirst_window_avg_cupnet"
MEANFIRST_LIVE_INLET = "cp_meanfirst_live_window_avg_inlet"
MEANFIRST_ALL_INLET = "cp_meanfirst_window_avg_inlet"


def meanfirst_threshold_name(label):
    return f"cp_meanfirst_live_exclusion_threshold_{label}_mol_m3"


def meanfirst_live_cm_name(label):
    return f"cm_area_mean_window_live_meanfirst_{label}"


EXTRA_SUMMARY_METRICS = (
    C_B_CUPNET_WINDOW,
    C_B_CUPNET_REL_DIFF,
    C_B_CUPNET_MAX_CELL_REL_DIFF,
    C_B_CUPNET_STATUS,
    MEANFIRST_LIVE_CUPNET,
    MEANFIRST_ALL_CUPNET,
    MEANFIRST_LIVE_INLET,
    MEANFIRST_ALL_INLET,
    meanfirst_threshold_name("cupnet"),
    meanfirst_threshold_name("inlet"),
    meanfirst_live_cm_name("cupnet"),
    meanfirst_live_cm_name("inlet"),
)


def salt_flowrate_report_name(boundary_index):
    return f"pp_salt_flowrate_unit_cell_boundary_{int(boundary_index)}"


def massflowrate_report_name(boundary_index):
    return f"pp_massflowrate_unit_cell_boundary_{int(boundary_index)}"


def boundary_cupnet_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{int(boundary_index)}_net_mol_m3"
    )


def cupnet_cell_name(cell_number):
    return f"pp_c_b_cupnet_cell_{int(cell_number)}_mol_m3"


def net_flux_cup_mass_fraction(salt_flow, mass_flow):
    """Net-flux cup mass fraction ``salt_flow / mass_flow``; raises with why."""
    if salt_flow is None or mass_flow is None:
        raise CupError("flow-rate report missing")
    salt, mass = float(salt_flow), float(mass_flow)
    if not (math.isfinite(salt) and math.isfinite(mass)):
        raise CupError(f"non-finite flow rate (salt {salt!r}, mass {mass!r})")
    if mass == 0.0:
        raise CupError("zero net mass flow")
    ratio = salt / mass
    if ratio <= 0.0:
        raise CupError(f"salt and mass flow disagree in sign (salt {salt!r}, mass {mass!r})")
    return ratio


def cupnet_window(
    flow_by_boundary,
    cell_numbers,
    weights_by_cell,
    *,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    cups_massavg_by_cell,
    c_b_cup_window,
):
    """Net-flux cup per boundary, per cell and window, plus its rows.

    ``flow_by_boundary``: ``{k: (salt_flow, mass_flow)}``. Never raises: a
    missing or unusable report leaves the window None and says why.
    """
    rows = []
    out = {
        C_B_CUPNET_WINDOW: None,
        C_B_CUPNET_REL_DIFF: None,
        C_B_CUPNET_MAX_CELL_REL_DIFF: None,
        C_B_CUPNET_STATUS: "",
    }
    factor = float(density_kg_per_m3) / float(molecular_weight_kg_per_mol)
    boundaries = sorted({k for cell in cell_numbers for k in (cell - 1, cell)})
    molar, problems = {}, []
    for k in boundaries:
        salt, mass = flow_by_boundary.get(k, (None, None))
        rows.append({"metric": salt_flowrate_report_name(k), "value": salt, "unit": "kg/s"})
        rows.append({"metric": massflowrate_report_name(k), "value": mass, "unit": "kg/s"})
        try:
            molar[k] = net_flux_cup_mass_fraction(salt, mass) * factor
        except CupError as exc:
            problems.append(f"boundary {k}: {exc}")
            molar[k] = None
        rows.append({"metric": boundary_cupnet_name(k), "value": molar[k], "unit": "mol/m3"})
    if problems:
        out[C_B_CUPNET_STATUS] = "blank: " + "; ".join(problems)
        return out, rows
    cells = {cell: 0.5 * (molar[cell - 1] + molar[cell]) for cell in cell_numbers}
    for cell in cell_numbers:
        rows.append({"metric": cupnet_cell_name(cell), "value": cells[cell], "unit": "mol/m3"})
    try:
        window = weighted_window(cells, weights_by_cell, cell_numbers)
    except CupError as exc:
        out[C_B_CUPNET_STATUS] = f"blank: {exc}"
        return out, rows
    worst = max(
        abs(cells[c] - float(cups_massavg_by_cell[c])) / float(cups_massavg_by_cell[c])
        for c in cell_numbers
    )
    out.update({
        C_B_CUPNET_WINDOW: window,
        C_B_CUPNET_REL_DIFF: (window - float(c_b_cup_window)) / float(c_b_cup_window),
        C_B_CUPNET_MAX_CELL_REL_DIFF: worst,
        C_B_CUPNET_STATUS: f"ok: max cell |net - massavg|/massavg {worst:.3%}",
    })
    return out, rows


def _close(a, b, rel=1e-12):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def inlet_cp_from_columns(wide, *, live, default_c_0=None):
    """Inlet-reference mean-first CP ``(cm - cp)/(c_0 - cp)`` for one leaf.

    Returns ``(value, status)``. A leaf extracted with the inlet columns is
    read directly. Otherwise:

    * all faces: ``cm_area_mean_window`` and ``cp_ref_area`` (always exact);
    * live: the saved live means apply only when their live threshold is
      ``c_0``. A cup-based leaf (``c_b_cup_window_mol_m3``) saved the
      mean-first live mean ``cm_area_mean_window_live_meanfirst`` with
      ``cp_meanfirst_live_exclusion_threshold_mol_m3``; an earlier leaf
      ``cm_area_mean_window_live`` with ``live_exclusion_threshold_mol_m3``.
      ``cm_area_mean_window_live`` on a cup-based leaf is the mid-plane
      face set and is never paired with ``cp_ref_area_live``.
    """
    saved_key = MEANFIRST_LIVE_INLET if live else MEANFIRST_ALL_INLET
    saved = _float(wide, saved_key)
    if saved is not None:
        return saved, "ok: extracted"
    c_0 = _float(wide, "c_inlet_ref_mol_m3")
    source = "c_inlet_ref_mol_m3"
    if c_0 is None:
        if default_c_0 is None:
            return None, "blank: no c_inlet_ref_mol_m3"
        c_0, source = float(default_c_0), "constant c_0 (column absent)"
    if live:
        if _float(wide, C_B_CUP_WINDOW) is not None:
            keys = (MEANFIRST_LIVE_CM, "cp_ref_area_live", MEANFIRST_LIVE_THRESHOLD)
        else:
            keys = ("cm_area_mean_window_live", "cp_ref_area_live", "live_exclusion_threshold_mol_m3")
    else:
        keys = ("cm_area_mean_window", "cp_ref_area", None)
    cm, cp_ref = _float(wide, keys[0]), _float(wide, keys[1])
    missing = [k for k, v in ((keys[0], cm), (keys[1], cp_ref)) if v is None]
    if missing:
        return None, "blank: no " + ", ".join(missing)
    if keys[2] is not None:
        threshold = _float(wide, keys[2])
        if threshold is None:
            return None, f"blank: no {keys[2]}"
        if not _close(threshold, c_0):
            return None, (
                f"blank: saved live set is c_p < {threshold:.6g}, not c_0 {c_0:.6g}; re-extract"
            )
    try:
        value = _ratio(cm, cp_ref, c_0)
    except CupError as exc:
        return None, f"blank: {exc}"
    return value, f"ok: recomputed from saved columns ({source})"


def cupnet_cp_from_columns(wide, *, live):
    """Net-flux cup CP: only a leaf extracted with the net-flux reports has it."""
    key = MEANFIRST_LIVE_CUPNET if live else MEANFIRST_ALL_CUPNET
    value = _float(wide, key)
    if value is not None:
        return value, "ok: extracted"
    status = str(wide.get(C_B_CUPNET_STATUS) or "").strip()
    if status:
        return None, status if status.startswith("blank") else f"blank: {status}"
    return None, "blank: not extracted with the net-flux reports (needs a re-extract)"
