# Diamond campaign config. All nine Diamond meshes are built and verified;
# this file now drives the solver sweep only.
#
# Mesh inventory (all with corrected schema v2 geometry metadata):
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
# D0817 ~0.724; spread within a pitch is under 0.4 percentage points).
# Surface skewness and volume ortho are monotone in angle: a30 best, a60
# worst. D0817_a60 has the lowest ortho (0.0676) against the 0.05 gate and
# is the first candidate for the mesh-independence study.
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
    "bl_layers": 4,
    "peel_layers": 2,
    "periodic_after_surface_mesh": True,
    "wall_spacer_labels": ["wall_spacer"],
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

# Bridge radius stays 1.10e-4 campaign-wide. brg156 was tried on the a60
# family and made things worse, not better:
#   D2450_a60  brg110 skew 0.6767 ortho 0.0849 AR  87.7 cells  946741
#              brg156 skew 0.6681 ortho 0.0804 AR  82.2 cells  861345
#   D1225_a60  brg110 skew 0.6977 ortho 0.0762 AR  79.4 cells 1059674
#              brg156 skew 0.6939 ortho 0.0734 AR 120.9 cells  874144
#   D0817_a60  brg110 skew 0.86115   brg156 skew 0.86783   (both fail 0.85)
# Surface skewness barely moved while volume ortho degraded and D1225_a60's
# aspect ratio jumped, so the worst surface face is not at the bridge node.
# D0817_a60 instead gets a finer surface size (m_max 0.060), which keeps the
# geometry identical across the family and records the difference in mesh_id:
# skewness fell 0.86115 -> 0.746625, well clear of the 0.85 gate.
#
# Mesh layouts, kept as reference data. Empty case list: nothing left to mesh.
# Restore a subset here to rebuild.
# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm, m_max)
_MESH_LAYOUTS = (
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

_MESH_CASES_TO_BUILD = ()

mesh_batch_cases = []
for _geo_id, _n_active, _pitch_mm, _periodic_dy_mm, _m_max in _MESH_LAYOUTS:
    if _geo_id not in _MESH_CASES_TO_BUILD:
        continue
    _spacing_code, _angle_token = _geo_id.split("_")
    _case_mesh_id = f"max{int(round(_m_max * 1000)):03d}_min006_cpg5_bl4_peel2"
    mesh_batch_cases.append({
        "family": _FAMILY,
        "geo_id": _geo_id,
        "mesh_id": _case_mesh_id,
        "spacing_code": _spacing_code,
        "attack_angle_deg": int(_angle_token[1:]),
        "n_active_cells": _n_active,
        "cell_length_x_m": _pitch_mm * 1.0e-3,
        "periodic_shift_y": _periodic_dy_mm,
        "m_max": _m_max,
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

# First multi-case solver run after the mesh rebuild. D2450_a45 only, three
# velocities at p6M.
#
# The pre-rebuild u0p2_p6M run must be deleted before this batch: its
# mesh_sha256 (222e7886...) predates the rebuild while the current mesh is
# dcf1bea4... Cell count and all four quality metrics reproduced exactly, so
# the geometry is identical and u0p2 should return u_mean 0.1992807169514518
# with inlet_profile_G 1.00360939613. The mesh manifest G is currently None:
# the rebuild reset the lazy-fill and the first run repopulates it.
#
# u0p1 is expected to quasi-converge (LMH stable, residual ~1.4e-6 at the
# iteration cap) rather than reach qoi_converged.
#
# batch_solver_sweep.py has none of the session-lifecycle hardening added to
# batch_meshing.py (no retry, no teardown wait, no settle delay). This batch
# is partly a test of whether the solver hits the same AttachAssembly /
# socket-reset contention across cases.
_SWEEP_GEO = "D2450_a45"
_SWEEP_MESH_ID = "max085_min006_cpg5_bl4_peel2"
_SWEEP_P = 6.0e6

solver_sweep_cases = []
for _u in (0.1, 0.2, 0.3):
    _run_id = make_base_case_name(_u, _SWEEP_P)
    solver_sweep_cases.append({
        "family": _FAMILY,
        "geo_id": _SWEEP_GEO,
        "mesh_id": _SWEEP_MESH_ID,
        "run_id": _run_id,
        "geo_name": _SWEEP_GEO,
        "case_name": _run_id,
        "inlet_velocity_value": _u,
        "outlet_gauge_pressure": _SWEEP_P,
    })
