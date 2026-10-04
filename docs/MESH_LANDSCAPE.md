# Mesh landscape

Parameter exploration, inventory, and convergence notes for the RO spacer
campaign. All exploration numbers below are on **D2450_a45** unless stated.
Campaign baseline mesh_id: `max085_min006_cpg5_bl4_peel2` (796,009 cells).

See also `docs/GEOMETRY_DESIGN.md` (joint spheres, Pillar bore, sinusoidal
curvature margin) and `docs/metrics_conventions.md` (coordinates, QoI
reliability).

---

## Parameter landscape (D2450_a45)

Baseline: `m_max` 0.085 / `m_min` 0.006 / `m_cpg` 5 / `bl` 4 / peel 2 →
**796,009** cells.

| change | cells | × baseline | notes |
|--------|------:|----------:|-------|
| `m_min` 0.003 | 803,653 | 1.01 | skew 0.671 → 0.625 (Δ −21%); near-free |
| `bl` 6 | 913,162 | 1.15 | |
| `m_cpg` 7 | 952,824 | 1.20 | **passed** (contradicts an archive note that it failed) |
| `bl` 8 | 1,050,560 | 1.32 | |
| `bl` 10 | 1,162,761 | 1.46 | ortho 0.0528, AR 139 (7% margin to gate 150) |
| `bl` 12 | 1,279,676 | 1.61 | AR 251.9 — **FAILED** |
| `m_min` 0.004 | 1,719,676 | 2.16 | non-monotone vs `m_min` 0.003 |
| `m_max` 0.060 | 4,280,354 | 5.38 | **cliff** |
| `m_max` 0.045 | 5,146,805 | 6.47 | |
| `m_max` 0.035 | 6,154,123 | 7.73 | skew rebounds to 0.704 |

### Key findings

- **`bl_height_factor` does not change the mesh under smooth-transition.**
  `bl8`, `bl8_f020`, and `bl8_f040` have different `mesh_sha256` but the
  same cell count and byte-identical physics (`cp_canon` 1.036991, \(c_b\)
  622.626, `lmh` 25.6599, ΔP 843.791, \(y_1\) 2.769 µm). The worker does
  apply the override: `bl_height = m_min * bl_height_factor` and that
  value is `FirstHeight` on Add Boundary Layers
  (`scripts/meshing_code_260616.py` 279–280 and 1043). Smooth-transition
  then ignores `FirstHeight` and sets prism thickness from layer count
  and growth rate. `_fNNN` is `round(factor × 100)` (`f040` = 0.40, not
  ×1000). `format_production_mesh_id` omits it. A study whose factor
  differs from the template puts the token back
  (`scripts/mfbo/mesh_study_case.py`).
- **\(y_1\) responds to both axes and is not separable:** bl4 5.736 µm, bl6
  3.985, bl8 2.769, bl10 1.926; `m_max` 0.045 at bl4 already gives 3.304 µm.
  Smooth-transition sets prism thickness from the adjacent core cell.
  Medians on D0817_a30 and P_p80_h15 from the 2026-10 screening are in
  the next section. They do not replace this D2450_a45 list.
- **`m_max` is cheap below the cliff:** 0.085 → 0.060 costs ×5.38; each step
  below that costs only ~×1.20.
- **Gate ceiling at campaign `m_max`:** `bl` 10 is the max that still passes
  (AR 139 of 150). `bl` 12 fails.

### Literature comparison (Liang et al., multi-layer spacer CFD)

| quantity | Liang et al. | this campaign |
|----------|--------------|---------------|
| BL layers | ≥ 20 | 4 |
| BL thickness | 2–4% of \(h\) = 15.4–30.8 µm | ~75 µm |
| core cell | 3–5% of \(h\) = 23–38.5 µm | ~59.5 µm |
| cell type | tet unstructured + prism | poly-hexcore |
| total cells | > 90 M | ~0.8–6 M on exploration |

They report needing ≥ 20 cells in the inflated boundary to hold GCI below
2.5% for spacer wall shear and mass-transfer coefficient. Our BL is roughly
**2.5× too thick with a fifth of the layers**. Poly-hexcore is the suspected
reason `bl` 12 blew up on aspect ratio where a tet core would transition
smoothly.

**Open question for the real mesh study:** can a thinner, deeper BL (Liang-like)
be realised under poly-hexcore without AR failure, or does the study need a
different core strategy?

`scripts/mfbo/mesh_study_case.py` is the driver for one such case. It
takes the production mesh template for `--geo-id`, requires at least one
of `--m-max`, `--m-min`, `--m-cpg`, `--bl-layers`,
`--bl-first-height-factor`, and refuses a `mesh_id` equal to production.
The unchanged baseline is
`scripts/mfbo/reextract_runs.py --copy-from-production`, which copies the
run and mesh leaves and re-extracts the copy. Neither driver opens
`C:/ro_data` in Fluent. Because smooth-transition ignores `FirstHeight`,
a factor-only study is not expected to move \(y_1\) until the offset
method changes.

---

## Mesh inventory (31 meshes; 279 solver runs)

**31** = 9 Diamond + 3 ML + 9 Pillar + 9 Sinusoidal + `REF_empty`.
Solver campaign is 31 meshes × 9 operating points = **279** runs.
All 31 meshes are built and their manifests validate. `P_p80_*_f320` is
not in the whitelist (no CAD).
Quality ordering at the campaign mesh settings:

| family | ortho | AR | why |
|--------|-------|-----|-----|
| Pillar | 0.204–0.315 | 21–30 | filaments never touch membrane; pillar ends flat |
| ML | 0.075–0.136 | 35–99 | contact wedges + joint spheres |
| Diamond | 0.068–0.115 | 55–100 | filament–membrane contact |

**Spacer wall zones (intentional asymmetry):**

| family | zones | count |
|--------|-------|------:|
| Diamond | `wall_spacer` | 1 |
| ML | `top`, `mid`, `bottom`, `bridge`, `buffer` | 5 |
| Pillar | `filament`, `pillar`, [`hole` if bored], `buffer` | 3–4 |
| Sinusoidal | `axial`, `bridge`, `buffer` | 3 |
| REF_empty | (none) | 0 |

Cross-family comparison is at the **total** spacer-wall level. Decomposition
is for within-family interpretation. Re-meshing Diamond to split labels would
invalidate its verified references (D2450_a45: 796,009 cells, physical bulk
velocity \(0.2\,\mathrm{m/s}\), `inlet_profile_G` 1.00360939613) — leave it.
`wall_spacer_buffer` is the buffer-cut face on every split family (ML, Pillar,
Sinusoidal).

Authoritative per-mesh quality lives in each mesh manifest under
`RO_DATA_ROOT/meshes/…/manifest.json`. Snapshot tables below are what the
repo documents; refresh from manifests when remeshing.

### Diamond (9)

Campaign `m_max` 0.085 except D0817_a60 (`m_max` 0.060). Zones = 1.

| geo | cells | skew | ortho | AR | eps |
|-----|------:|-----:|------:|---:|----:|
| D2450_a30 | 1,499,175 | 0.6160 | 0.10124 | 68.42 | 0.9088 |
| D2450_a45 | 796,009 | 0.6706 | 0.10209 | 62.77 | 0.9088 |
| D2450_a60 | 946,741 | 0.6767 | 0.08492 | 87.66 | 0.9093 |
| D1225_a30 | 1,393,328 | 0.6239 | 0.11532 | 69.92 | 0.8167 |
| D1225_a45 | 558,090 | 0.6500 | 0.09262 | 64.15 | 0.8169 |
| D1225_a60 | 1,059,674 | 0.6977 | 0.07616 | 79.39 | 0.8170 |
| D0817_a30 | 1,604,696 | 0.6301 | 0.10014 | 63.76 | 0.7234 |
| D0817_a45 | 502,794 | 0.6446 | 0.09305 | 64.82 | 0.7268 |
| D0817_a60 | 1,751,893 | 0.7466 | 0.06759 | 99.61 | 0.7238 |

`eps` is set by pitch, not angle. Surface skew and volume ortho are monotone
in angle (a30 best, a60 worst).

### ML (3)

All at `max085_min006_cpg5_bl4_peel2`. Host dump from mesh manifests.

| geo | cells | skew | ortho | AR | sff | eps | brg R | zn |
|-----|------:|-----:|------:|---:|----:|----:|------:|--:|
| M_c160 | 2,003,036 | 0.6104 | 0.10104 | 58.41 | 0 | 0.9321 | 0.000100 | 5 |
| M_c267 | 1,748,357 | 0.6106 | 0.07496 | 98.51 | 0 | 0.9319 | 0.000105 | 5 |
| M_c400 | 2,064,340 | 0.5704 | 0.13589 | 35.40 | 0 | 0.9131 | 0.000110 | 5 |

M_c267 was ortho 0.00940 (failing the 0.05 gate) at R = 0.110 before the
wedge-coverage update to R = 0.105.

### Pillar (9)

Same mesh_id. Bore axis `{0, 0.15, 0.30}` (`h00` / `h15` / `h30`).

| geo | cells | skew | ortho | AR | sff | eps | brg R | zn |
|-----|------:|-----:|------:|---:|----:|----:|------:|--:|
| P_p60_h00 | 995,035 | 0.5604 | 0.20390 | 21.23 | 0 | 0.8973 | 0 | 3 |
| P_p60_h15 | 1,244,514 | 0.5365 | 0.24096 | 23.91 | 0 | 0.8987 | 0 | 4 |
| P_p60_h30 | 1,217,258 | 0.4518 | 0.20200 | 24.37 | 0 | 0.9040 | 0 | 4 |
| P_p80_h00 | 947,020 | 0.5006 | 0.31494 | 24.23 | 0 | 0.8795 | 0 | 3 |
| P_p80_h15 | 1,214,673 | 0.5251 | 0.28074 | 29.04 | 0 | 0.8815 | 0 | 4 |
| P_p80_h30 | 1,138,172 | 0.4777 | 0.20954 | 23.43 | 0 | 0.8876 | 0 | 4 |
| P_p100_h00 | 897,940 | 0.4582 | 0.22506 | 24.64 | 0 | 0.8543 | 0 | 3 |
| P_p100_h15 | 1,201,883 | 0.4944 | 0.31426 | 28.36 | 0 | 0.8568 | 0 | 4 |
| P_p100_h30 | 1,055,859 | 0.5358 | 0.25932 | 29.80 | 0 | 0.8645 | 0 | 4 |

### Sinusoidal (9)

3×3 complete (a072 / a144 / a193 × l1733 / l3465 / l6930). Same campaign
mesh_id. Zones = 3 (`axial`, `bridge`, `buffer`). Sub-unity curvature case
`S_a193_l1733` is acknowledged in `docs/GEOMETRY_DESIGN.md`. Authoritative
per-geo quality is in each mesh manifest; the case that closed the matrix:

| geo | cells | skew | ortho | AR | eps |
|-----|------:|-----:|------:|---:|----:|
| S_a193_l1733 | 4,977,088 | 0.602 | 0.12175 | 48.41 | 0.80629 |

Quality is inside campaign range. `S_a193_l6930` is worse (ortho 0.1118,
skew 0.640). Porosity vs `S_a144_l1733`: 0.80629 vs 0.79949.

### Import zone counts

First geometry import prints `N boundary face zones` (= recognised CAD
named selections). Verified on all 44 leaves: diamond 11, ml 15, pillar
h00 13, pillar h15/h30 14, sin 13, REF_empty 10. See
`docs/GEOMETRY_DESIGN.md`.

### Meshing is deterministic

Three re-meshes with identical CAD and parameters reproduced cell count,
`ortho_min`, `AR_max`, and skewness to all printed digits: ML c160/c267,
`REF_empty`, and `S_a193_l1733`.

`print_meshing_input_summary` used Python `print()` while the PyFluent
transcript captures only Fluent session output, so no pre-existing mesh_log
contains the `Periodic translation [mm]` line that
`parse_meshing_input_summary` expects. The worker now also appends that
block to `mesh_log_*.txt`. Sin, ML re-meshes, and `REF_empty` logs carry
it; older Diamond/Pillar logs do not.

### Observations

**Quality ordering across families.** Pillar (ortho 0.204–0.315, AR 21–30) is
markedly better than ML (0.075–0.136, AR 35–99), which is better than Diamond
(0.068–0.115, AR 55–100). Pillar filaments never touch the membrane so there
is no contact wedge, and the pillar ends are flat so no cusp forms.

**Porosity confirms two design intentions.** M_c160 and M_c267 land at 0.9321
and 0.9319, 0.02 percentage points apart, so the equal-volume control pair is
realised even more closely than the 0.7% the solid-volume coefficients
predicted; M_c400 sits 1.9 points lower at 0.9131 because its thick filament
occupies the 90-degree layer. On Pillar, eps falls with pillar diameter
(0.897 / 0.880 / 0.855 at h00) and rises with bore, but non-linearly: h15 adds
only about a quarter of what h30 adds, consistent with bore volume scaling as
diameter squared.

---

## Screening mesh study (session 2026-09-29 .. 2026-10-02)

Recorded 2026-10-04 from the workstation paste. Run id `u0p2_p6M`.
Changes are relative to production bl4. This screening does not close
the D2450_a45 `bl` or `m_max` axes in the parameter landscape above.

Production reference leaves:

- `C:/ro_data/runs/diamond/D0817_a30/max085_min006_cpg5_bl4_peel2/u0p2_p6M/`
- `C:/ro_data/runs/pillar/P_p80_h15/max085_min006_cpg5_bl4_peel2/u0p2_p6M/`

`scripts/mfbo/mesh_study_case.py` writes the variants under a
`--data-root` that it refuses to set to `C:/ro_data`. That root's path
was not in the paste. Mesh ids below are what
`format_production_mesh_id` builds when the other knobs stay at the
production template (`m_max` 0.085, `m_min` 0.006, `m_cpg` 5, peel 2).
The paste did not print those id strings.

| geo_id | knob | mesh_id | CP-excess change vs bl4 | \(\Delta P\) change vs bl4 |
|---|---|---|---:|---:|
| D0817_a30 | `m_max` 0.060 | `max060_min006_cpg5_bl4_peel2` | +22% | −4.6% |
| D0817_a30 | `m_cpg` 7 | `max085_min006_cpg7_bl4_peel2` | +18% | −0.8% |
| D0817_a30 | `bl` 8 | `max085_min006_cpg5_bl8_peel2` | +4.4% | +7.4% |
| D0817_a30 | `m_min` 0.004 | `max085_min004_cpg5_bl4_peel2` | negligible | negligible |
| P_p80_h15 | `bl` 8 | `max085_min006_cpg5_bl8_peel2` | +6.4% | +1.2% |
| P_p80_h15 | `m_max` | (value not repeated in the paste) | +1.2% | (not stated) |
| P_p80_h15 | `m_cpg` 7 | `max085_min006_cpg7_bl4_peel2` | −0.9% | (not stated) |
| P_p80_h15 | `m_min` 0.004 | `max085_min004_cpg5_bl4_peel2` | negligible | negligible |

`CP_q99` / `CP_q999` move 10–36% across this screening. The paste does
not assign that range to one knob.

\(y_1\) median, µm. Smooth-transition still ignores `FirstHeight`
(parameter-landscape finding above); these medians are the layer-count
response on these two geometries.

| | D0817_a30 | P_p80_h15 |
|---|---:|---:|
| bl4 | 3.69 | 5.58 |
| bl8 | 1.77 | 2.69 |

### Split boundary layer (commit `5fe1d38`)

Membrane 8 layers, spacer 4. With the other production knobs the mesh id
is `max085_min006_cpg5_bl8s4_peel2`.

Measured: on P_p80_h15, CP matches bl8 and \(\Delta P\) matches bl4, and
the cell count is −16% (the paste does not name the baseline of that
percentage). On D0817_a30, CP excess is −20% versus bl8, outside the
bl4..bl8 range. Mesh quality was normal. The difference grows downstream.

**Session conclusion:** split BL is unsafe for contact geometries.
**Hypothesis:** the miss is a boundary-layer transition at the contact
wedges. The downstream growth and the departure from the bl4..bl8
bracket are the measurements that conclusion uses.

---

## Convergence

### Post-hoc gate

Pass when all of:

- `|lmh_relative_difference| < 1e-3`
- `|mass_balance_relative_error| < 1e-3`
- `continuity_final < 1e-4`

Independent of solver `stop_reason`.

**Removed from the gate:** unit-cell pressure-drop spread. That spread
is converged-state physics (2.58% at \(u=0.2\), 14.7% at \(u=0.3\) on the
continuity 4–7 column), unchanged between the 301-iteration and
765-iteration solutions. It is recorded as
`pp_pressure_drop_rel_spread_window` (WARNING;
`evaluation_window.evaluation_cell_numbers(layout)`) plus the continuity
column `pp_pressure_drop_rel_spread_cells_4_7`.

### Independence study (D2450_a45, p = 6 MPa)

2000 iterations with QoI stop off vs the 301-iteration QoI stop:

| run | result |
|-----|--------|
| u0p1 | `max_iter_reached`; continuity floors at \(4.2\times10^{-7}\) and never reaches \(10^{-7}\); `lmh_udm_avg` fixed to 9 decimals from iteration 298 |
| u0p2 | `residual_converged` at 379; every quantity identical to 6 sig figs vs 301 stop |
| u0p3 | `residual_converged` at 765; CP −0.010%, ΔP +0.074%; `lmh_relative_difference` went \(-0.2925 \rightarrow 1.30\times10^{-4}\) |

So **`qoi_initial_values_to_ignore` does not need raising**: the 301 stop
never detected a plateau. `ignore=200` plus \(N_p=100\) makes 301 the first
legal window, and LMH's period-2 oscillation (\(\sim10^{-6}\)) is three
orders inside the \(10^{-3}\) relative criterion, so the window is already
closed the moment it may fire. **301 is not adequate for production
runs.** At \(u=0.3\) that first-legal stop had
`lmh_relative_difference` \(-0.2925\) and `mass_balance_relative_error`
\(0.226\); the quality gate correctly returned FAIL. What 301 missed is
exactly what that gate catches. The live solver stop is now
`all-conditions-are-met` on `lmh_udm_avg` **and** `pressure_drop_spacer`
(same relative `stop_criterion` \(10^{-3}\)), with residual
`check_convergence` still on. UG 37.18 All includes enabled residuals, so
the solver stops on LMH ∧ spacer ΔP ∧ residuals.

The previous `any-condition-is-met` LMH-only stop is why u0p3 declared
`qoi_converged` at iteration 301 while continuity was still
\(6.4\times10^{-3}\).

### Turbulence check (session 2026-09-29 .. 2026-10-02)

Recorded 2026-10-04 from the workstation paste. Laminar campaign leaves
are under `C:/ro_data`, production mesh
`max085_min006_cpg5_bl4_peel2`. SST and realizable k-ε leaves are the
copies `scripts/mfbo/solve_campaign_case.py` writes under a study data
root that is not `C:/ro_data`. That root's path was not in the paste.
The driver appends `_sst` (`k-omega-sst`) or `_rke`
(`k-epsilon-realizable-ewt`) to the base run id.

Production laminar leaves for the cases named below:

- `C:/ro_data/runs/pillar/P_p100_h00/max085_min006_cpg5_bl4_peel2/u0p3_p6M/`
- `C:/ro_data/runs/pillar/P_p100_h00/max085_min006_cpg5_bl4_peel2/u0p2_p6M/`
- `C:/ro_data/runs/pillar/P_p100_h30/max085_min006_cpg5_bl4_peel2/u0p2_p6M/`
- `C:/ro_data/runs/diamond/D0817_a30/max085_min006_cpg5_bl4_peel2/u0p3_p6M/`
- `C:/ro_data/runs/diamond/D1225_a30/max085_min006_cpg5_bl4_peel2/u0p3_p6M/`
- `C:/ro_data/runs/diamond/D2450_a30/max085_min006_cpg5_bl4_peel2/u0p3_p6M/`
- `C:/ro_data/runs/pillar/P_p100_h15/max085_min006_cpg5_bl4_peel2/u0p3_p6M/`

Study leaves, same mesh id, run ids `u0p3_p6M_sst`, `u0p3_p6M_rke` on
`P_p100_h00`, and `u0p2_p6M_sst` on `P_p100_h30`.

Measured on `P_p100_h00` at `u0p3`: laminar continuity 0.54. SST failed
(continuity 0.61, \(\mu_t/\mu\) average 0.10, \(D_{\mathrm{eff}}/D\)
average 68). Realizable k-ε with enhanced wall treatment reached a stop
at 301 iterations with \(\mu_t/\mu\) average 3.2 (maximum 29) and
\(D_{\mathrm{eff}}/D\) average about 2000.

**Session reading of those k-ε ratios:** the field that stopped is
model-made.

**Withdrawn (2026-10-04).** That `u0p3` comparison was also read as
realizable k-ε turbulent salt diffusion lowering CP. The laminar side
was the non-converged `u0p3` run (continuity 0.54). The controls below,
against residual-converged laminar, show realizable k-ε raises
CP-excess. That direction is withdrawn. The ratios and the 301-iteration
stop above stay.

Measured control, `P_p100_h30` at `u0p2`: SST and laminar agree within
0.1% on LMH, \(\Delta P\), and CP. \(\mu_t/\mu\) average is 0.034.

**Session conclusion, drawn from this control and the `P_p100_h00`
RANS attempt:** laminar is appropriate as the production viscous model.
The 2026-10-04 controls below decide the realizable k-ε `u0p3` field.
**Hypothesis:** the \(u = 0.3\) laminar failures have no steady
solution because the flow has become unsteady. They are treated that way.
The gate metrics of the original rejects stay in
`docs/STATUS_2026-09-22.md`; this check does not replace that table.

Failed at `u0p3`: `D0817_a30`, `D1225_a30`, `D2450_a30`, `P_p100_h15`,
`P_p100_h00`. `P_p100_h00` also fails at `u0p2`. `D2450_a45` at `u0p3`
in the independence study above is a different geometry and did converge.

#### Controls on converged laminar cases (2026-10-04)

Recorded 2026-10-04 from the workstation paste. Turbulent leaves are
under `C:/ro_data_mfbo/turb`. Laminar references are production runs
under `C:/ro_data`, mesh id `max085_min006_cpg5_bl4_peel2`, all
`residual_converged`, PASS. The paste did not name the workstation git
commit. Run ids follow `scripts/mfbo/solve_campaign_case.py`: `_sst` is
`k-omega-sst`, `_rke` is `k-epsilon-realizable-ewt`.

Laminar references:

- `C:/ro_data/runs/pillar/P_p100_h00/max085_min006_cpg5_bl4_peel2/u0p1_p6M/`
- `C:/ro_data/runs/pillar/P_p100_h30/max085_min006_cpg5_bl4_peel2/u0p2_p6M/`

Turbulent leaves:

- `C:/ro_data_mfbo/turb/runs/pillar/P_p100_h00/max085_min006_cpg5_bl4_peel2/u0p1_p6M_sst/`
- `C:/ro_data_mfbo/turb/runs/pillar/P_p100_h00/max085_min006_cpg5_bl4_peel2/u0p1_p6M_rke/`
- `C:/ro_data_mfbo/turb/runs/pillar/P_p100_h30/max085_min006_cpg5_bl4_peel2/u0p2_p6M_rke/`

All three turbulent runs are `residual_converged`, PASS. Deltas are
versus the laminar reference of the same geometry and inlet velocity.

| leaf | \(\mu_t/\mu\) avg (max) | \(D_{\mathrm{eff}}/D\) avg | LMH | \(\Delta P\) | CP-excess avg | CP max excess |
|---|---|---:|---:|---:|---:|---:|
| `P_p100_h00` `u0p1_p6M_sst` | 0.0074 (4.2) | 5.8 | +0.01% | +0.09% | −0.27% | −1.96% |
| `P_p100_h00` `u0p1_p6M_rke` | 0.069 (1.86) | 45 | −0.45% | −0.15% | +10.4% | (not stated) |
| `P_p100_h30` `u0p2_p6M_rke` | 1.09 (12.4) | 698 | −0.79% | −1.40% | +28.8% | +20.3% |

**Session conclusion, drawn from this table and the earlier `P_p100_h30`
`u0p2` SST control:** SST reproduces the steady laminar LMH, \(\Delta P\),
and average CP-excess on the cases measured. Realizable k-ε changes those
steady laminar results: CP-excess average is higher than the converged
laminar reference on both RKE leaves. The `P_p100_h00` realizable k-ε
`u0p3` field is therefore not a usable laminar solution. The laminar
`u0p3` leaf for this geometry failed the gate, so that unusability is
inferred from these controls and the earlier `u0p3` ratios.
**Hypothesis (unverified):** the larger RKE departure on `P_p100_h30` at
`u0p2` than on `P_p100_h00` at `u0p1` is a velocity effect. Those leaves
are different geometries, so the paste does not separate velocity from
geometry.
**Hypothesis (unverified):** realizable k-ε eddy viscosity damps
spacer-induced secondary flows near the membrane and weakens convective
mixing.

### \(u = 0.1\) saturation patch

A stagnation region near the first filament exceeds NaCl saturation. At 2000
iterations it sits entirely in the entrance buffer
(\(x = 0.003515\)–\(0.003731\,\mathrm{m}\)) with **zero** wall area above
\(Y_i = 0.26\) in evaluation cells 5–8. `n_lead_excluded = 3` excludes it.
At 301 iterations it appeared in cell 7 — an **under-convergence artifact**,
not a contact-band feature of the converged field.
