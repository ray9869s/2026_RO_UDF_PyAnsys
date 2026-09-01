# Mesh sensitivity Phase 0: near-wall (concentration boundary layer) resolution
# on the most-verified geometry, D2450_a45.
#
# Why this study: Sc = nu/D = 1e-6/2e-9 = 500, so the concentration boundary
# layer is Sc^(-1/3) = 0.126 of the momentum layer, roughly 13-48 um.
# bl_height = m_min * bl_height_factor = 0.006 * 0.4 = 2.4 um nominal, but the
# prism first layer measures ~14 um (y1 = 6 um, matching the documented 5-7x
# inflation), so only 1-3 cells sit inside the concentration layer. Published
# high-Sc mass-transfer work uses ~3 um cells or 20 inflation layers.
# Internal evidence: the analytic wall reconstruction cuts CP grid dependence
# from ~30% to ~8%, i.e. the base mesh does not resolve it.
#
# Core resolution is NOT the concern. vol_hex_max 59.5 um across a 770 um
# channel matches published grid convergence at 0.1 mm cell size.
#
# Judge convergence on probe_cp_reconstruction's cp_raw (cell-centre), not
# cp_recon: the reconstruction masks the grid error. Baseline at bl4/f040 is
# cp_raw 1.06505 vs cp_recon 1.08708, a 2.07% gap. If bl12/f015 closes that to
# under 0.5%, the near-wall grid is adequate.
#
# bl4_f040 is geometrically identical to the current campaign mesh, so
# cell_count must come back as 796009. That also confirms the
# bl_height_factor override reaches the mesher.
#
# periodic_after_surface_mesh = True is the campaign default: setting periodic
# boundaries before surface meshing produces shadow-copy node slivers
# (measured as max skewness 0.88 vs 0.67 on D2450_a45).
#
# buffer_wall_base_names MUST be overridden: this geometry splits buffer walls
# into _in/_out, and zone_matches_base_name only accepts "base" or "base.N".
#
# D0817_a45 depends on the archived 8/11 CAD (1487160 B); the 8/16 re-save
# passes surface meshing but fails prism generation.
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

# bl_layers and bl_height_factor are deliberately NOT set here; each case
# supplies them so the sensitivity levels are explicit at the case level.
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
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
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
# Phase 0 sensitivity cases
# ---------------------------------------------------------------------------
_SENS_GEO = "D2450_a45"
_SENS_M_MAX = 0.085

# (bl_layers, bl_height_factor) -> bl_height nominal / measured first layer
_BL_LEVELS = (
    ( 4, 0.40),   # 2.4 um / ~14 um   <- current campaign setting
    ( 8, 0.20),   # 1.2 um / ~7 um
    (12, 0.15),   # 0.9 um / ~5 um
)


def _sens_mesh_id(bl_layers, bl_height_factor):
    return (
        f"max{int(round(_SENS_M_MAX * 1000)):03d}_min006_cpg5"
        f"_bl{bl_layers}_f{int(round(bl_height_factor * 100)):03d}_peel2"
    )


mesh_batch_cases = []
for _bl, _f in _BL_LEVELS:
    mesh_batch_cases.append({
        "family": _FAMILY,
        "geo_id": _SENS_GEO,
        "mesh_id": _sens_mesh_id(_bl, _f),
        "spacing_code": "D2450",
        "attack_angle_deg": 45,
        "n_active_cells": 7,
        "cell_length_x_m": 0.003465,
        "periodic_shift_y": 3.465,
        "m_max": _SENS_M_MAX,
        "bl_layers": _bl,
        "bl_height_factor": _f,
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

# One operating point per mesh level. u0p2_p6M is the mid-range case and the
# only one where the 301-iteration QoI stop reproduced the 2000-iteration
# solution to 6 significant figures on every reported quantity, so it isolates
# the mesh effect cleanly.
#
# The QoI stop stays enabled (run_config default). The convergence-independence
# study settled that: at p=6 MPa on D2450_a45, running 2000 iterations with the
# stop off gave
#   u0p2  residual_converged at 379; all quantities identical to the 301 stop
#   u0p3  residual_converged at 765; CP -0.010%, dP +0.074%
#   u0p1  max_iter_reached; continuity floors at 4.2e-07, lmh_udm_avg fixed to
#         9 decimals from iteration 298
# so qoi_initial_values_to_ignore does not need raising. What the 301 stop
# missed on u0p3 is caught by the post-hoc lmh_relative_difference gate.
solver_sweep_cases = []
for _bl, _f in _BL_LEVELS:
    solver_sweep_cases.append({
        "family": _FAMILY,
        "geo_id": _SENS_GEO,
        "mesh_id": _sens_mesh_id(_bl, _f),
        "run_id": "u0p2_p6M",
        "geo_name": _SENS_GEO,
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    })

# ---------------------------------------------------------------------------
# Campaign mesh layouts, reference data. Not used by this Phase 0 config.
# All nine are already built with bl4 / f040 (mesh_id without the _fNNN token):
#   geo         mesh_id  cells     skew    ortho    AR      eps
#   D2450_a30   max085   1499175   0.6160  0.10124  68.42   0.9088
#   D2450_a45   max085    796009   0.6706  0.10209  62.77   0.9088
#   D2450_a60   max085    946741   0.6767  0.08492  87.66   0.9093
#   D1225_a30   max085   1393328   0.6239  0.11532  69.92   0.8167
#   D1225_a45   max085    558090   0.6500  0.09262  64.15   0.8169
#   D1225_a60   max085   1059674   0.6977  0.07616  79.39   0.8170
#   D0817_a30   max085   1604696   0.6301  0.10014  63.76   0.7234
#   D0817_a45   max085    502794   0.6446  0.09305  64.82   0.7268
#   D0817_a60   max060   1751893   0.7466  0.06759  99.61   0.7238
#
# porosity_eps is set by pitch, not angle (D2450 ~0.909, D1225 ~0.817,
# D0817 ~0.724; spread within a pitch under 0.4 percentage points). Surface
# skewness and volume ortho are monotone in angle: a30 best, a60 worst.
# D0817_a60 has the lowest ortho (0.0676) against the 0.05 gate.
#
# Bridge radius stays 1.10e-4 campaign-wide. brg156 was tried on the a60
# family and made things worse:
#   D2450_a60  brg110 skew 0.6767 ortho 0.0849 AR  87.7 cells  946741
#              brg156 skew 0.6681 ortho 0.0804 AR  82.2 cells  861345
#   D1225_a60  brg110 skew 0.6977 ortho 0.0762 AR  79.4 cells 1059674
#              brg156 skew 0.6939 ortho 0.0734 AR 120.9 cells  874144
#   D0817_a60  brg110 skew 0.86115   brg156 skew 0.86783   (both fail 0.85)
# The worst surface face is not at the bridge node. D0817_a60 uses a finer
# surface size (m_max 0.060) instead, which drops skewness to 0.746625.
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
