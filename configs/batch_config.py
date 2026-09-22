# ML and Pillar meshing, second attempt with the actual CAD named selections.
#
# ---------------------------------------------------------------------------
# What the first attempt found
# ---------------------------------------------------------------------------
# 8 of 12 succeeded. The 4 failures were CAD named-selection problems, not
# quality problems:
#
#   M_c160 / M_c267 / M_c400
#     Fluent reported the only available label as m_c160-solid etc. — the CAD
#     has NO named selections at all, not even inlet/outlet/periodic.
#   P_p80_h20
#     Has all ten common labels but is missing the four wall_spacer_* ones.
#     Its siblings P_p80_h00 and P_p80_h30 have them.
#
# And the label names I had guessed were wrong. The real ones are:
#   ML      wall_spacer_top, wall_spacer_mid, wall_spacer_bottom,
#           wall_spacer_bridge, wall_spacer_buffer
#   Pillar  wall_spacer_filament, wall_spacer_pillar, wall_spacer_hole,
#           wall_spacer_buffer
#
# wall_spacer_buffer is the spacer cross-section where the buffer solid cuts
# it — a bluff face normal to the incoming flow. It was absent from the first
# config, so the 8 successful Pillar meshes got NO local sizing and NO
# boundary layers there. That has to be rebuilt for two reasons:
#   1. Diamond puts that same cut face inside its single wall_spacer label, so
#      it does receive sizing and BL there. Leaving it out of ML/Pillar makes
#      the family comparison uneven.
#   2. The downstream cut sits at x = 27.72 mm, which is the end of evaluation
#      cell 8 — inside the window, not in the discarded buffer.
#   3. Rule 3-3 raises when a wall_spacer_* zone exists in the mesh but is
#      absent from spacer_wall_zones, so the solver would refuse these meshes
#      anyway.
#
# ---------------------------------------------------------------------------
# Pillar quality from the first attempt: clearly better than Diamond
# ---------------------------------------------------------------------------
#   geo            cells    skew    ortho     AR      eps
#   P_p60_h00     995231  0.5620  0.24514   31.08  0.8973
#   P_p60_h20    1234919  0.4551  0.21281   29.36  0.9000
#   P_p60_h30    1212043  0.4728  0.20026   33.43  0.9040
#   P_p80_h00     945759  0.5340  0.23893   29.75  0.8795
#   P_p80_h30    1134808  0.4714  0.20649   30.61  0.8876
#   P_p100_h00    893858  0.5142  0.13948   47.39  0.8543
#   P_p100_h20   1141534  0.4969  0.22959   33.45  0.8588
#   P_p100_h30   1048280  0.5298  0.18165   34.73  0.8645
#
# Against Diamond's skew 0.616-0.747, ortho 0.068-0.115, AR 55-100: ortho is
# 2-3x better and AR is roughly half. The filaments never touch the membrane,
# so there is no contact wedge, and the pillar ends are flat, so no cusp.
# eps lands where predicted in order and magnitude (0.854 / 0.880 / 0.897 for
# D_p 1.00 / 0.80 / 0.60) and matches Qamar 2021's 0.88 pillar / 0.90
# hole-pillar. Adding a bore raises eps as it should: h00 0.8973 -> h20 0.9000
# -> h30 0.9040 at D_p 0.60.
#
# Note P_p60_h30 has fewer cells than P_p60_h20 (1212043 vs 1234919): a larger
# bore simplifies the mesh.
#
# ---------------------------------------------------------------------------
# What the Diamond exploration established (all on D2450_a45, u0p2_p6M)
# ---------------------------------------------------------------------------
# BUG FOUND AND FIXED: the mid-plane c_b was sampled at z = h/2 = +0.000385,
# the UPPER MEMBRANE. The inlet face centre is the origin, so z runs -h/2 to
# +h/2 and the mid-plane is z = 0. A candidate list [h/2, 0.0] with
# first-success-wins always picked the wall. After the fix c_b went
# 622.63 -> 598.72, canonical CP moved +2.7%, and c_b became grid-insensitive
# (0.03% across the whole sweep) where it had looked like a 2.58%
# core-resolution effect.
#
# Cell-count landscape, baseline 796,009 at m_max 0.085 / m_min 0.006 /
# m_cpg 5 / bl 4 / peel 2:
#   m_min 0.003     803,653   x1.01   skew 0.671 -> 0.625   <- near-free
#   bl 6            913,162   x1.15
#   m_cpg 7         952,824   x1.20   passed (archive had it failing)
#   bl 8          1,050,560   x1.32
#   bl 10         1,162,761   x1.46   ortho 0.0528, AR 139 (7% margin)
#   bl 12         1,279,676   x1.61   AR 251.9  FAILED
#   m_min 0.004   1,719,676   x2.16   non-monotone vs 0.003
#   m_max 0.060   4,280,354   x5.38   <- cliff
#   m_max 0.045   5,146,805   x6.47
#   m_max 0.035   6,154,123   x7.73   skew rebounds to 0.704
#
# Metric reliability for the real mesh study:
#   cm_mol_m3_avg   monotone, artifact-free  <- primary indicator
#   c_b             stable to 0.03%          <- sanity check
#   lmh             monotone, gentle
#   pressure_drop   grid-independent (0.30% across bl4-bl10)
#   cp_canon        contaminated by local facet artifacts, NOT usable
#   cp_canon_max    varies by orders of magnitude, useless
#
# Post-processing costs ~39 min per case on the smallest mesh (REF_empty,
# 628k cells); ~80% is PyFluent settings-API round trips. See
# docs/POSTPROCESSING_MAP.md.
#
# continue_on_failure stays True: a CAD or gate failure on a new family is a
# result, and the four bad CAD files may still be mid-repair.
# ---------------------------------------------------------------------------
from ro.campaign_matrix import (
    PRODUCTION_MESH_ID_D0817_A60,
    build_production_mesh_batch_cases,
    build_production_solver_sweep_cases,
)
from ro.solver_common import make_base_case_name

dry_run = False
continue_on_failure = True
skip_existing_mesh = True
skip_existing_final_data = True

# Batch meshing session lifecycle (AttachAssembly / socket-reset contention).
# inter_case_delay_s: settle time after one worker exits before the next starts.
# post_failure_settle_s: floor applied after any non-SUCCESS case before the
#   next case (even when inter_case_delay_s is 0). Leftover Fluent/Discovery/
#   CADReaders processes are scanned after this settle.
# transient_failure_max_retries: retries after CAD AttachAssembly, socket-
#   reset, Scheme heap, or Fluent launch/spawn death (2 => up to 3 total
#   worker invocations for that case). Solver sweep uses the same knobs.
# clean_fm_scratch_on_success: remove FM_<HOST>_<PID>/ dirs after SUCCESS*.
inter_case_delay_s = 0.0
post_failure_settle_s = 15.0
transient_failure_max_retries = 2
clean_fm_scratch_on_success = True

# ---------------------------------------------------------------------------
# Shared meshing controls
#
# Held at the Diamond campaign settings so the first ML/Pillar meshes are
# directly comparable. m_min stays 0.006 rather than the near-free 0.003
# improvement, because changing it here would confound a family comparison
# with a resolution change on the very first build.
#
# Unit cell is 3.465 x 3.465 mm for every case below, every family uses
# theta = 45 deg, so cell_length_x_m = periodic_shift_y = 3.465 throughout.
# n_active_cells = 7 with the 1 + 7 + 2 buffer layout, giving active cells
# 2-8 (x = 3.465 to 27.72 mm) and evaluation cells 5-8 after
# n_lead_excluded = 3.
# ---------------------------------------------------------------------------
_COMMON_MESH = {
    "overlap_m": 0.0,
    "n_buffer_in": 1,
    "n_buffer_out": 2,
    "buffer_length_in_m": 0.003465,
    "buffer_length_out_m": 0.00693,
    "n_lead_excluded": 3,
    "n_trail_excluded": 0,
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "peel_layers": 2,
    "periodic_after_surface_mesh": True,
    "attack_angle_deg": 45,
    "n_active_cells": 7,
    "cell_length_x_m": 0.003465,
    "periodic_shift_y": 3.465,
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

common_mesh_settings = dict(_COMMON_MESH)
common_mesh_settings["filament_d_m"] = 4.0e-4
common_mesh_settings["bridge_radius_m"] = 1.10e-4
common_mesh_settings["wall_spacer_labels"] = ["wall_spacer"]

_MESH_ID = "max085_min006_cpg5_bl4_peel2"

# ---------------------------------------------------------------------------
# Multi-Layer: three layer-thickness distributions, Sigma_d = 0.800 mm fixed
#
# Layers top / middle / bottom at +45 / 90 / -45 degrees. The 90-degree middle
# layer blocks the flow head-on and is this family's dominant lever, so c400
# (middle 0.400, the same diameter as a Diamond filament) should give the
# largest dP and the strongest mixing.
#
# Joint spheres: three filaments pass through the same (x, y), so the two
# tangent contacts sit on one vertical line at z = 0.385 +/- r_middle. Two
# spheres per node, one at each contact. Radii from cusp coverage at g = 12 um
# (see campaign_geometry._ML_GEOMETRY); R may exceed r_min.
#
#   geo      diameters t/m/b     contact z         sphere R   contact width
#   M_c160   0.320/0.160/0.320   0.305, 0.465      0.100      0.135
#   M_c267   0.266670 x3         0.25167, 0.51833  0.105      0.123
#   M_c400   0.200/0.400/0.200   0.185, 0.585      0.110      0.105
#
# Solid-volume coefficients (2*d_o^2 + sqrt(2)*d_c^2)/h^2 are 0.241 / 0.243 /
# 0.306, so c160 and c267 are within 0.7% of each other — the clean pair for
# attributing results to vertical distribution alone — while c400 is 26%
# heavier because the thick filament sits in the denser 90-degree layer at
# pitch 1732.5. Report eps alongside any c400 conclusion.
#
# filament_d_m carries the middle-layer diameter; the manifest's full geometry
# (all three layer diameters, axis heights, sphere centres) comes from
# campaign_geometry.py.
#
# (geo_id, filament_d_m, bridge_radius_m)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Added 2026-09-05: from claude
#   geo      diameters t/m/b     contact z         sphere R   R_s
#   M_c160   0.320/0.160/0.320   +/-0.080          0.100      0.4189
#   M_c267   0.266670 x3         +/-0.133335       0.105      0.4552
#   M_c400   0.200/0.400/0.200   +/-0.200          0.110      0.5236
#
# Sphere radius from R = sqrt(2 * R_s * g) with g = 12 um (2 * m_min), where
# R_s is the shallow principal radius of the paraboloidal gap at a 45 degree
# cylinder crossing. Capped by R <= 1.414 * r_min from the sphere-cylinder
# intersection angle, which binds on c160 at 0.1131. The old values
# (0.070 / 0.110 / 0.110) followed no rule; R/r_min was 0.875 / 0.825 / 1.100.
# ---------------------------------------------------------------------------

_ML_SPACER_LABELS = [
    "wall_spacer_top",
    "wall_spacer_mid",
    "wall_spacer_bottom",
    "wall_spacer_bridge",
    "wall_spacer_buffer",
]

_ML_CASES = (
    ("M_c160", 1.60e-4, 1.00e-4),
    ("M_c267", 2.66670e-4, 1.05e-4),
    ("M_c400", 4.00e-4, 1.10e-4),
)

# ---------------------------------------------------------------------------
# Pillar / Hole-Pillar: 3 pillar diameters x 3 bore sizes
#
# Single coplanar filament layer at z = 0 (mid-height), d_f = 0.400, so
# clearance is (0.770 - 0.400)/2 = 0.185 per side, c/h = 0.240. Qamar 2021
# holds 0.35 mm clearance in a 1.2 mm channel, c/h = 0.292, so this is close
# to the literature while keeping d_f identical to Diamond. That makes Pillar
# a single-variable perturbation of Diamond: same diameter, same pitch, two
# layers merged onto the mid-plane with pillars taking over support.
#
# No filament-filament joint sphere: coplanar filaments fully interpenetrate
# at the node and the pillar covers it, so bridge_radius_m is 0.
#
# Unit cell is 3.465 x 3.465 mm (same as ML / Sin / CAD), not 4*D_p.
# Consumed membrane_blocked_area_frac is 0.0 campaign-wide (LMH on nominal
# area). Geometric pillar footprints 4.7/8.4/13.1% (pi r^2 over the 6.0025
# mm^2 node at pitch 2450 / 45 deg) live only in
# membrane_blocked_area_frac_geometric.
#
# Bore axis is {0, 0.15, 0.30} mm absolute diameter (geo tokens h00 / h15 /
# h30), not a ratio of D_p, so the same jet orifice is tested at every pillar
# size and the two axes stay orthogonal. Residual filament shell at the
# crossing is r_f - r_h: 0.125 mm at d_h 0.150 and 0.050 mm at d_h 0.300.
#
# Why not h20 (d_h = 0.20): on the pillar surface at z = 0 each opening has
# half-angle arcsin(r/R_p). Filaments at +/-45 deg and bore at 0 leave an arc
# gap R_p * (45deg - arcsin(r_f/R_p) - arcsin(r_h/R_p)) (um; + separate,
# - merged):
#
#                  h10        h15        h20        h30
#     p60  R 0.300  -33.5      -59.1      -85.3     -140.4     (all merged)
#     p80  R 0.400  +54.6      +29.3      +3.65      -49.0
#     p100 R 0.500  +136.9     +111.7     +86.3      +34.6
#
# Confirmed in Discovery: p80_h20 = 3.64 um, p100_h30 = 34.6 um. 3.65 um is
# below m_min = 6 um, so the surface mesher cannot resolve that sliver.
# Four P_p80_h20 attempts all failed the 0.85 skew gate:
#     old CAD, cpg5              skew 0.89298  37 skewed faces
#     rebuilt CAD, cpg5          skew 0.90116  37
#     rebuilt CAD, m_min 0.003   skew 0.89656  39
#     rebuilt CAD, m_cpg 7       skew 0.88967  22   (462,440 surface faces)
# Forcing a merge needs d_h >= 0.207 and a barely-merged pair meets at a cusp;
# a safe merge is ~0.28-0.30, i.e. h30. So the middle level moved to 0.15 mm
# (smallest remaining gap 29 um on p80_h15, comparable to p100_h30 which
# already meshed at skew 0.4702).
#
# D_p = 0.60 interpretation: the two filament openings are only 33 um apart
# on the pillar surface (90deg - 2*arcsin(0.200/0.300) = 6.38deg), so the
# pillar is already almost fully cut through by the filaments. Any bore
# merges with them; an isolated hole would need d_h < 0.033. "Hole-pillar"
# is therefore not geometrically realised at D_p = 0.60 — those cases are
# enlarged openings, not a separate jet orifice. State that when interpreting
# p60 h15/h30 results.
#
# wall_spacer_hole is only listed for h15 and h30. Rule 3-3 raises if an h00
# case declares it, which is the guard against a misapplied boolean.
#
# (pillar_D_mm, bore_d_mm) -> geo_id P_p{D*100}_h{d*100}
# ---------------------------------------------------------------------------
_PILLAR_D_MM = (0.60, 0.80, 1.00)
_PILLAR_H_MM = (0.00, 0.15, 0.30)

def _pillar_geo_id(d_mm, h_mm):
    return f"P_p{int(round(d_mm * 100)):d}_h{int(round(h_mm * 100)):02d}"


def _pillar_spacer_labels(h_mm):
    labels = ["wall_spacer_filament", "wall_spacer_pillar"]
    if h_mm > 0.0:
        labels.append("wall_spacer_hole")
    labels.append("wall_spacer_buffer")
    return labels


mesh_batch_cases = []

for _geo_id, _fil_d_m, _brg_m in _ML_CASES:
    _case = dict(_COMMON_MESH)
    _case.update({
        "family": "ml",
        "geo_id": _geo_id,
        "mesh_id": _MESH_ID,
        "spacing_code": _geo_id,
        "filament_d_m": _fil_d_m,
        "bridge_radius_m": _brg_m,
        "wall_spacer_labels": list(_ML_SPACER_LABELS),
    })
    mesh_batch_cases.append(_case)

for _d_mm in _PILLAR_D_MM:
    for _h_mm in _PILLAR_H_MM:
        _case = dict(_COMMON_MESH)
        _case.update({
            "family": "pillar",
            "geo_id": _pillar_geo_id(_d_mm, _h_mm),
            "mesh_id": _MESH_ID,
            "spacing_code": _pillar_geo_id(_d_mm, _h_mm),
            "filament_d_m": 4.00e-4,
            "bridge_radius_m": 0.0,
            "wall_spacer_labels": _pillar_spacer_labels(_h_mm),
        })
        mesh_batch_cases.append(_case)

# ---------------------------------------------------------------------------
# Sinusoidal: 3 half-amplitudes x 3 streamwise wavelengths (9 cases).
# ---------------------------------------------------------------------------
_SIN_SPACER_LABELS = [
    "wall_spacer_axial",
    "wall_spacer_bridge",
    "wall_spacer_buffer",
]

_SINUSOIDAL_GEO_IDS = tuple(
    f"S_{amplitude}_l{wavelength}"
    for amplitude in ("a072", "a144", "a193")
    for wavelength in ("1733", "3465", "6930")
)

for _geo_id in _SINUSOIDAL_GEO_IDS:
    _case = dict(_COMMON_MESH)
    _case.update({
        "family": "sin",
        "geo_id": _geo_id,
        "mesh_id": _MESH_ID,
        "spacing_code": _geo_id,
        "attack_angle_deg": 0,
        "n_active_cells": 7,
        "cell_length_x_m": 3.465e-3,
        "periodic_shift_y": 3.465,
        "filament_d_m": 8.0e-4,
        "bridge_radius_m": 0.0,
        "wall_spacer_labels": list(_SIN_SPACER_LABELS),
    })
    mesh_batch_cases.append(_case)

# ---------------------------------------------------------------------------
# Empty reference channel: no spacer; dP/LMH baseline with the standard 1+7+2
# layout and inlet-face-centre origin like every other family.
# ---------------------------------------------------------------------------
_case = dict(_COMMON_MESH)
_case.update({
    "family": "empty",
    "geo_id": "REF_empty",
    "mesh_id": _MESH_ID,
    "spacing_code": "REF",
    "attack_angle_deg": 0,
    "filament_d_m": 0.0,
    "bridge_radius_m": 0.0,
    "wall_spacer_labels": [],
})
mesh_batch_cases.append(_case)

# ---------------------------------------------------------------------------
# Solver: nothing this round. Meshing is the gate and CAD probe; solve once
# the quality results are in and all twelve CAD files are confirmed good.
# ---------------------------------------------------------------------------
common_solver_settings = {
    "run_calculation_enabled": True,
    "max_iterations": 2000,
    "residual_target": 1e-7,
    "operating_pressure": 101325.0,
    "buffer_wall_base_names": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
    "use_inlet_velocity_profile": True,
}

# ---------------------------------------------------------------------------
# Solver: default exploratory list is the four u=0.3 restarts from the
# same-geometry converged u=0.2 finals. A new run_id (u0p3_p6M_restart)
# writes a new leaf so the rejected u0p3_p6M result is not overwritten.
# Restore solver_sweep_cases = _SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS after
# this experiment. --case-set production is still the 279.
# ---------------------------------------------------------------------------
_MESH_ID_BL6 = "max085_min006_cpg5_bl6_peel2"
_RO_RUNS = "C:/ro_data/runs"


def _restart_from_u0p2_finals(family, geo_id):
    leaf = f"{_RO_RUNS}/{family}/{geo_id}/{_MESH_ID}/u0p2_p6M"
    stem = f"{geo_id}_u0p2_p6M"
    return {
        "restart_from_case_file": f"{leaf}/{stem}_final.cas.h5",
        "restart_from_data_file": f"{leaf}/{stem}_final.dat.h5",
    }


_SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS = [
    {
        "family": "empty",
        "geo_id": "REF_empty",
        "mesh_id": _MESH_ID,
        "run_id": "u0p2_p6M",
        "geo_name": "REF_empty",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "family": "empty",
        "geo_id": "REF_empty",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M",
        "geo_name": "REF_empty",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": _MESH_ID,
        "run_id": "u0p2_p8M",
        "geo_name": "D2450_a45",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": _MESH_ID_BL6,
        "run_id": "u0p2_p8M",
        "geo_name": "D2450_a45",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "family": "diamond",
        "geo_id": "D0817_a45",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M",
        "geo_name": "D0817_a45",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "family": "diamond",
        "geo_id": "D0817_a60",
        "mesh_id": PRODUCTION_MESH_ID_D0817_A60,
        "run_id": "u0p2_p6M",
        "geo_name": "D0817_a60",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "family": "diamond",
        "geo_id": "D0817_a30",
        "mesh_id": _MESH_ID,
        "run_id": "u0p2_p6M",
        "geo_name": "D0817_a30",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    },
]

solver_sweep_cases = [
    {
        "family": "diamond",
        "geo_id": "D0817_a30",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M_restart",
        "geo_name": "D0817_a30",
        "case_name": "u0p3_p6M_restart",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
        **_restart_from_u0p2_finals("diamond", "D0817_a30"),
    },
    {
        "family": "diamond",
        "geo_id": "D1225_a30",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M_restart",
        "geo_name": "D1225_a30",
        "case_name": "u0p3_p6M_restart",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
        **_restart_from_u0p2_finals("diamond", "D1225_a30"),
    },
    {
        "family": "pillar",
        "geo_id": "P_p100_h15",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M_restart",
        "geo_name": "P_p100_h15",
        "case_name": "u0p3_p6M_restart",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
        **_restart_from_u0p2_finals("pillar", "P_p100_h15"),
    },
    {
        "family": "sin",
        "geo_id": "S_a144_l1733",
        "mesh_id": _MESH_ID,
        "run_id": "u0p3_p6M_restart",
        "geo_name": "S_a144_l1733",
        "case_name": "u0p3_p6M_restart",
        "inlet_velocity_value": 0.3,
        "outlet_gauge_pressure": 6.0e6,
        **_restart_from_u0p2_finals("sin", "S_a144_l1733"),
    },
]

# Production 31-mesh / 279-run matrix. Distinct names and a distinct
# --case-set production entrypoint. mesh_batch_cases (22) stays the
# exploratory mesh list. solver_sweep_cases is the four u0p3 restarts.
# Layout knobs come from the registry, never from a _COMMON_MESH copy.
production_mesh_batch_cases = build_production_mesh_batch_cases(_COMMON_MESH)
production_solver_sweep_cases = build_production_solver_sweep_cases(
    production_mesh_batch_cases
)

# ---------------------------------------------------------------------------
# Diamond campaign meshes, built. Reference data, not used by this config.
#   geo         m_max  cells     skew    ortho    AR      eps
#   D2450_a30   0.085  1499175   0.6160  0.10124  68.42   0.9088
#   D2450_a45   0.085   796009   0.6706  0.10209  62.77   0.9088
#   D2450_a60   0.085   946741   0.6767  0.08492  87.66   0.9093
#   D1225_a30   0.085  1393328   0.6239  0.11532  69.92   0.8167
#   D1225_a45   0.085   558090   0.6500  0.09262  64.15   0.8169
#   D1225_a60   0.085  1059674   0.6977  0.07616  79.39   0.8170
#   D0817_a30   0.085  1604696   0.6301  0.10014  63.76   0.7234
#   D0817_a45   0.085   502794   0.6446  0.09305  64.82   0.7268
#   D0817_a60   0.060  1751893   0.7466  0.06759  99.61   0.7238
#
# porosity_eps is set by pitch, not angle. Surface skewness and volume ortho
# are monotone in angle: a30 best, a60 worst.
#
# Bridge radius stays 1.10e-4 for Diamond. brg156 on the a60 family made
# things worse:
#   D2450_a60  brg110 skew 0.6767 ortho 0.0849 AR  87.7 cells  946741
#              brg156 skew 0.6681 ortho 0.0804 AR  82.2 cells  861345
#   D1225_a60  brg110 skew 0.6977 ortho 0.0762 AR  79.4 cells 1059674
#              brg156 skew 0.6939 ortho 0.0734 AR 120.9 cells  874144
#   D0817_a60  brg110 skew 0.86115   brg156 skew 0.86783   (both fail 0.85)
# D0817_a60 uses a finer surface size (m_max 0.060) instead, dropping
# skewness to 0.746625.
#
# D0817_a45 depends on the archived 8/11 CAD (1487160 B); the 8/16 re-save
# passes surface meshing but fails prism generation.
#
# Diamond keeps a single wall_spacer label while ML and Pillar are split. The
# asymmetry is intentional: cross-family comparison happens at the total
# level, decomposition is for within-family interpretation, and re-meshing
# Diamond would invalidate its verified references.
#
# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm, m_max)
# ---------------------------------------------------------------------------
_DIAMOND_MESH_LAYOUTS = (
    ("D2450_a45",  7, 3.465,         3.465,        0.085),
    ("D1225_a45", 14, 1.7325,        1.7325,       0.085),
    ("D0817_a45", 21, 1.155,         1.155,        0.085),
    ("D2450_a30",  9, 2.8291606522,  4.900249992,  0.085),
    ("D1225_a30", 18, 1.4145803261,  2.450124996,  0.085),
    ("D0817_a30", 27, 0.9430535507,  1.633416664,  0.085),
    ("D2450_a60",  5, 4.900249992,   2.8291606522, 0.085),
    ("D1225_a60", 10, 2.450124996,   1.4145803261, 0.085),
    ("D0817_a60", 15, 1.633416664,   0.9430535507, 0.060),
)
