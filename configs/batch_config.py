# Mesh sensitivity Phase 0: near-wall (concentration boundary layer) resolution
# on the most-verified geometry, D2450_a45.
#
# Why: Sc = nu/D = 1e-6/2e-9 = 500, so the concentration boundary layer is
# Sc^(-1/3) = 0.126 of the momentum layer, roughly 13-48 um. run_config already
# records the problem: bl4 -> bl6 moved window CP from 1.039 to 1.051, a 1.2%
# shift against a ~0.6% CP discriminability signal, so CP is NOT grid-converged
# in that range. Measured y1 (first-cell centroid, area-weighted) is bl4 mean
# 5.736 um / max 16.14, bl6 mean 3.985 um / max 6.19. Published high-Sc mass-
# transfer work uses ~3 um cells or 20 inflation layers. And the analytic wall
# reconstruction cuts CP grid dependence from ~30% to ~8%, which is itself
# evidence the base mesh does not resolve the layer.
#
# Core resolution is NOT the concern. vol_hex_max 59.5 um across a 770 um
# channel matches published grid convergence at 0.1 mm.
#
# AXIS: bl_layers only, bl_height_factor pinned at the run_config default 0.40.
# The earlier bl8/f020 and bl12/f015 attempts changed BOTH knobs and paid for
# it in volume quality:
#   bl4  / f040   796,009 cells  ortho 0.10209  AR  62.77   PASS
#   bl8  / f020 1,050,560 cells  ortho 0.07463  AR 124.13   PASS
#   bl12 / f015 1,279,676 cells  ortho 0.05057  AR 251.94   FAILED
# bl12 hit BOTH gates: AR over the 150 ceiling and ortho at the 0.05 floor.
# run_config calls AR 150 "a campaign ceiling, not a quality target", but ortho
# 0.0506 is a real numerical limit. Thinning the first layer (f020, f015) is
# what flattens cells; adding layers at fixed f040 should keep AR near 62.8
# while smooth-transition still compresses y1 (the documented bl4 -> bl6 drop
# from 5.736 to 3.985 um happened at fixed factor).
#
# JUDGE ON cp_raw, not cp_recon. probe_cp_reconstruction prints both; the
# reconstruction masks the grid error. Baseline at bl4/f040 is cp_raw 1.06505
# vs cp_recon 1.08708, a 2.07% gap. Closing that under 0.5% means the near-wall
# grid resolves the concentration layer on its own.
#
# CAUTION for the campaign: D0817_a60 already sits at ortho 0.06759 with bl4.
# Whatever level wins here may not survive on the a60 geometries, which is the
# case for splitting membrane and spacer boundary layers. run_config exposes
# include_spacer_in_boundary_layers as an on/off knob today, but mesh_id has no
# token for it, so that probe needs a naming decision first.
#
# periodic_after_surface_mesh = True is the campaign default: setting periodic
# boundaries before surface meshing produces shadow-copy node slivers
# (max skewness 0.88 vs 0.67 on D2450_a45).
#
# buffer_wall_base_names MUST be overridden: this geometry splits buffer walls
# into _in/_out, and zone_matches_base_name only accepts "base" or "base.N".
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

# bl_layers and bl_height_factor are supplied per case so the sensitivity
# levels stay explicit; they are deliberately absent here.
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
# Phase 0 sensitivity levels
#
# The _fNNN token is bl_height_factor x100 (f040 = 0.40), matching the two
# meshes already on disk. run_config's comment says x1000; that comment needs
# correcting, but renaming would force a re-mesh.
#
# (bl_layers, bl_height_factor)
#   4 / 0.40  built, 796,009 cells, ortho 0.10209, AR  62.77  <- campaign
#   6 / 0.40  new; run_config records y1 mean 3.985 um and window CP 1.051
#   8 / 0.40  new; pure layer-count extension
#   8 / 0.20  built, 1,050,560 cells, ortho 0.07463, AR 124.13 (factor probe)
# skip_existing_mesh=True means the two built meshes are skipped and only
# bl6/f040 and bl8/f040 are generated, while all four get solved.
# ---------------------------------------------------------------------------
_SENS_GEO = "D2450_a45"
_SENS_M_MAX = 0.085

_BL_LEVELS = (
    ( 4, 0.40),
    ( 6, 0.40),
    ( 8, 0.40),
    ( 8, 0.20),
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
# The QoI stop stays at the run_config default (enabled). The convergence-
# independence study settled that: at p=6 MPa on D2450_a45, running 2000
# iterations with the stop off gave
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
# All nine are built with bl4 / f040 (mesh_id carries no _fNNN token):
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
# D0817_a60 has the lowest ortho, 0.06759 against the 0.05 gate.
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
