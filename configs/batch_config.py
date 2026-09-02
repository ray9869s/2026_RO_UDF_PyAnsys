# EXPLORATION PHASE, not a convergence study.
#
# Purpose: map how each meshing parameter scales cell count, where each hits
# the quality gates, and which combinations are computationally unreachable.
# The real mesh-independence study comes later, using the ranges this phase
# identifies. Nothing here is a final campaign setting.
#
# Method: one axis at a time from the campaign baseline (m_max 0.085,
# m_min 0.006, m_cpg 5, bl 4, peel 2) on D2450_a45. Meshing sweeps wide;
# the solver runs on six corners afterwards.
#
# ---------------------------------------------------------------------------
# What Phase 0 already established
# ---------------------------------------------------------------------------
# bl_height_factor is INERT. bl8_f020 and bl8_f040 have different
# mesh_sha256 but produce byte-identical physics:
#   cp_canon 1.036991, c_b 622.626, lmh 25.6599, dP 843.791, y1 2.769 um
# The smooth-transition offset method ignores the specified first height and
# sets thickness from layer count and growth rate alone. So the BL axis is
# bl_layers only, and the _fNNN mesh_id token is retired (MESH_ID_RE keeps it
# optional, we simply stop emitting it).
#
# BL sweep at m_max 0.085, all gates passed except bl12:
#   bl4   796,009 cells  ortho 0.10209  AR  62.77  y1 5.736 um
#   bl6   913,162        ortho 0.08920  AR  79.49  y1 3.985
#   bl8 1,050,560        ortho 0.07463  AR 124.13  y1 2.769
#   bl12/f015 1,279,676  ortho 0.05057  AR 251.94  FAILED AR and ortho
#
# Solver response at u0p2_p6M:
#          c_b       lmh      dP       cp_canon   delta
#   bl4  610.485  25.8773  846.265   1.038702  6.44e-05
#   bl6  616.347  25.7664  846.089   1.033545  2.29e-04
#   bl8  622.626  25.6599  843.791   1.036991  3.14e-04
#
# CP is NOT converged: -0.497% then +0.333%, non-monotone, both steps the size
# of the ~0.6% discriminability signal. But c_b, lmh and dP are each monotone.
# c_b rises 2.0% across the sweep despite being a mid-plane (z = 0) quantity
# the wall BL does not touch directly: adding prism layers pushes the
# poly-hexcore core inward and changes what the mid-plane clip cuts. Since c_b
# is the canonical CP denominator, core resolution is implicated too, which is
# why m_max joins the sweep here.
#
# ---------------------------------------------------------------------------
# Axis rationale
# ---------------------------------------------------------------------------
# bl_layers   near-wall. Concentration layer is 13-48 um (Sc = 500 gives
#             Sc^(-1/3) = 0.126 of the momentum layer); y1 must sit well
#             inside it. Expect ortho and AR to degrade monotonically.
# m_max       core. vol_hex_max = 0.7 * m_max, so 59.5 / 42 / 31.5 / 24.5 um
#             across a 770 um channel = 13 / 18 / 24 / 31 cells. Cell count
#             should scale near-cubically.
# m_min       curvature and proximity floor. Drives refinement at the bridge
#             spheres and the filament-membrane contact flats, so it is the
#             candidate for the cm_max extremes and the rising delta.
# m_cpg       cells per gap. Archive records cpg7 FAILING the skewed-face-
#             fraction gate at 8.22e-05 with max skew 0.9996, so this is a
#             confirmation probe, not an expected win.
#
# continue_on_failure stays True: a gate failure is a result, not an abort.
# ---------------------------------------------------------------------------
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
# transient_failure_max_retries: retries after CAD AttachAssembly or socket-
#   reset (2 => up to 3 total worker invocations for that case).
# clean_fm_scratch_on_success: remove FM_<HOST>_<PID>/ dirs after SUCCESS*.
inter_case_delay_s = 0.0
post_failure_settle_s = 15.0
transient_failure_max_retries = 2
clean_fm_scratch_on_success = True

_FAMILY = "diamond"
_EXPLORE_GEO = "D2450_a45"

# Meshing parameters are supplied per case so every level is explicit.
common_mesh_settings = {
    "filament_d_m": 4.0e-4,
    "bridge_radius_m": 1.10e-4,
    "overlap_m": 0.0,
    "n_buffer_in": 1,
    "n_buffer_out": 2,
    "buffer_length_in_m": 0.003465,
    "buffer_length_out_m": 0.00693,
    "n_lead_excluded": 3,
    "n_trail_excluded": 0,
    "peel_layers": 2,
    "periodic_after_surface_mesh": True,
    "wall_spacer_labels": ["wall_spacer"],
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

# ---------------------------------------------------------------------------
# Mesh exploration grid
# (bl_layers, m_max, m_min, m_cpg)
# ---------------------------------------------------------------------------
_EXPLORE = (
    # baseline (already built as max085_min006_cpg5_bl4_peel2)
    ( 4, 0.085, 0.006, 5),

    # BL axis. bl6 and bl8 exist under _f040 names and will be rebuilt here
    # without the token; identical physics is expected and is itself a check.
    ( 6, 0.085, 0.006, 5),
    ( 8, 0.085, 0.006, 5),
    (10, 0.085, 0.006, 5),
    (12, 0.085, 0.006, 5),

    # core axis
    ( 4, 0.060, 0.006, 5),
    ( 4, 0.045, 0.006, 5),
    ( 4, 0.035, 0.006, 5),

    # surface-minimum axis
    ( 4, 0.085, 0.004, 5),
    ( 4, 0.085, 0.003, 5),

    # cells-per-gap probe
    ( 4, 0.085, 0.006, 7),
)


def _explore_mesh_id(bl_layers, m_max, m_min, m_cpg):
    return (
        f"max{int(round(m_max * 1000)):03d}"
        f"_min{int(round(m_min * 1000)):03d}"
        f"_cpg{m_cpg}_bl{bl_layers}_peel2"
    )


mesh_batch_cases = []
for _bl, _mmax, _mmin, _cpg in _EXPLORE:
    mesh_batch_cases.append({
        "family": _FAMILY,
        "geo_id": _EXPLORE_GEO,
        "mesh_id": _explore_mesh_id(_bl, _mmax, _mmin, _cpg),
        "spacing_code": "D2450",
        "attack_angle_deg": 45,
        "n_active_cells": 7,
        "cell_length_x_m": 0.003465,
        "periodic_shift_y": 3.465,
        "m_max": _mmax,
        "m_min": _mmin,
        "m_cpg": _cpg,
        "bl_layers": _bl,
    })

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
# Solver corners
#
# Six cases at u0p2_p6M, the mid-range operating point and the only one where
# the 301-iteration QoI stop reproduced the 2000-iteration solution to six
# significant figures on every reported quantity, so it isolates the mesh
# effect cleanly.
#
# Baseline plus the extreme of each axis plus one crossing point. If the
# crossing lands where the sum of the two single-axis shifts predicts, the
# axes are independent and can be converged separately in the real study. If
# not, the interaction needs a full grid.
#
# Any corner whose mesh failed a gate is silently absent from the solver list
# because the mesh file will not exist; batch_solver_sweep reports it.
# ---------------------------------------------------------------------------
_SOLVER_CORNERS = (
    ( 4, 0.085, 0.006, 5),   # baseline
    ( 8, 0.085, 0.006, 5),   # BL axis, known-good
    (10, 0.085, 0.006, 5),   # BL axis, further
    ( 4, 0.045, 0.006, 5),   # core axis
    ( 4, 0.085, 0.003, 5),   # surface-minimum axis
    ( 8, 0.045, 0.006, 5),   # crossing: BL x core
)

_CORNER_U = 0.2
_CORNER_P = 6.0e6
_CORNER_RUN_ID = make_base_case_name(_CORNER_U, _CORNER_P)

solver_sweep_cases = []
for _bl, _mmax, _mmin, _cpg in _SOLVER_CORNERS:
    solver_sweep_cases.append({
        "family": _FAMILY,
        "geo_id": _EXPLORE_GEO,
        "mesh_id": _explore_mesh_id(_bl, _mmax, _mmin, _cpg),
        "run_id": _CORNER_RUN_ID,
        "geo_name": _EXPLORE_GEO,
        "case_name": _CORNER_RUN_ID,
        "inlet_velocity_value": _CORNER_U,
        "outlet_gauge_pressure": _CORNER_P,
    })

# ---------------------------------------------------------------------------
# Campaign mesh layouts, reference data. Not used by this exploration config.
# All nine are built at m_max as listed, min006, cpg5, bl4, peel2:
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
# Cross-family caution for the real study: D0817_a60 already sits at ortho
# 0.06759 and AR 99.61 with bl4. Whatever BL level wins on D2450_a45 may not
# survive there, which is the case for splitting membrane and spacer boundary
# layers. run_config exposes include_spacer_in_boundary_layers as an on/off
# knob, but mesh_id has no token for it, so that probe needs a naming decision
# first.
#
# porosity_eps is set by pitch, not angle (D2450 ~0.909, D1225 ~0.817,
# D0817 ~0.724; spread within a pitch under 0.4 percentage points). Surface
# skewness and volume ortho are monotone in angle: a30 best, a60 worst.
#
# Bridge radius stays 1.10e-4 campaign-wide. brg156 on the a60 family made
# things worse:
#   D2450_a60  brg110 skew 0.6767 ortho 0.0849 AR  87.7 cells  946741
#              brg156 skew 0.6681 ortho 0.0804 AR  82.2 cells  861345
#   D1225_a60  brg110 skew 0.6977 ortho 0.0762 AR  79.4 cells 1059674
#              brg156 skew 0.6939 ortho 0.0734 AR 120.9 cells  874144
#   D0817_a60  brg110 skew 0.86115   brg156 skew 0.86783   (both fail 0.85)
# The worst surface face is not at the bridge node. D0817_a60 uses a finer
# surface size (m_max 0.060) instead, dropping skewness to 0.746625.
#
# D0817_a45 depends on the archived 8/11 CAD (1487160 B); the 8/16 re-save
# passes surface meshing but fails prism generation.
#
# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm, m_max)
# ---------------------------------------------------------------------------
_CAMPAIGN_MESH_LAYOUTS = (
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
