# Metrics and coordinate conventions

## Mesh coordinate convention

**The inlet face centre is the origin.** On the campaign channel-centred mesh:

| axis | range | meaning |
|------|-------|---------|
| \(x\) | \(0\) → \(+L\) | streamwise; inlet at \(x = 0\) |
| \(y\) | \(-W/2\) → \(+W/2\) | spanwise |
| \(z\) | \(-h/2\) → \(+h/2\) | membrane-normal; **mid-plane at \(z = 0\)** |

Confirmed on D2450_a45 by the solver mesh check:

- \(x\): \(0\) to \(3.465\times10^{-2}\,\mathrm{m}\)
- \(y\): \(-1.732961\times10^{-3}\) to \(1.733456\times10^{-3}\,\mathrm{m}\)
- \(z\): \(-3.850994\times10^{-4}\) to \(3.851833\times10^{-4}\,\mathrm{m}\)

**This caused a real bug.** Post-processing once sampled \(c_b\) at \(z = h/2\) (the upper membrane) via a first-success-wins candidate list \([h/2,\,0]\). After fixing to \(z = \tfrac{1}{2}(z_{\min}+z_{\max})\) (= \(0\) here), \(c_b\) went \(622.63 \rightarrow 598.72\), canonical CP moved \(+2.7\%\), and \(c_b\) became grid-insensitive (\(0.03\%\) across the exploration sweep) where it had looked like a core-resolution effect.

Do **not** assume a bottom-origin frame where the mid-plane would be
\(z = h/2\). Post-processing and the geometry registry use the campaign frame
(mid-plane at \(z = 0\)). Registry `layer_axis_z_m` / `joint_sphere_z_m` are
derived in that frame (outer axes at \(\pm(r_{\mathrm{mid}}+r_{\mathrm{out}})\),
spheres at \(\pm r_{\mathrm{mid}}\)).

---

# CP modulus conventions

This document defines how concentration polarization (CP) modulus is computed
in post-processing for the RO spacer campaign. The UDF stores per-face CP in
UDM-9 using average-of-ratios; post-processing rescales and aggregates without
re-solving.

## Three definitions (literature mapping)

All three are evaluated on membrane wall faces within the **evaluation window**
(active cells after lead/trail exclusions), on both membranes, then
area-weighted.

| Label | Formula | Literature |
|-------|---------|------------|
| **Canonical** | \(M = (c_m - c_p)/(c_b - c_p)\) | Bae 2023 (JWPE 53, 103887) Eq. 12, un-approximated with local \(c_b\) |
| **L1** | \(M = c_m / c_b\) | Gu 2017 (JMS 527, 78–91) |
| **L2** | \(M = (c_m - c_p)/(c_0 - c_p)\) | Bae 2023 approximated form (inlet \(c_0\) denominator) |

- **Canonical** is the campaign definition for spacer comparison.
- **L1** and **L2** exist only for the definition-sensitivity table; they are
  not interchangeable with canonical CP.

### Field definitions

- \(c_m\): wall concentration from film reconstruction (`RO_ANALYTIC_CWALL = 1`,
  UDM-7 after analytic wall reconstruction).
- \(c_p\): face-local permeate-side reference \(J_s/J_w = B c_m/(J_w+B)\) (same
  as the flux boundary condition).
- \(c_b\): mid-plane (\(z = \tfrac{1}{2}(z_{\min}+z_{\max})\), equal to \(z = 0\) on the campaign channel-centred mesh) **mass-weighted** (mixing-cup) salt
  concentration [mol/m³], clipped in \(x\) to each evaluation-window unit cell
  (not the whole domain). Implemented as Fluent `surface-massavg` on an
  x-range iso-clip of the mid-plane iso-surface. Do not use
  `surface-areaavg` for \(c_b\): area-weighted species averages are not
  conserved across buffer regions with no membrane source (see campaign
  probe STEP F / `_tmp_cell_profile.py`).
- \(c_0\): inlet reference concentration (`c_inlet_ref`, 597.8268309 mol/m³).

## Metric reliability (grid exploration)

D2450_a45 at `u0p2_p6M`, varying mesh parameters around the campaign baseline
(`m_max` 0.085 / `m_min` 0.006 / `m_cpg` 5 / `bl` 4 / peel 2):

| QoI | Behaviour | Role |
|-----|-----------|------|
| `cm_mol_m3_avg` | monotone, artifact-free | **primary grid indicator** |
| \(c_b\) | stable to \(0.03\%\) once sampled at \(z = 0\) | sanity check |
| `lmh` | monotone, gentle | secondary |
| `pressure_drop` | grid-independent (\(0.30\%\) across bl4–bl10) | secondary |
| `cp_canon` | contaminated by local facet artifacts | **not usable for grid judgement** |
| `cp_canon_max` | varies by orders of magnitude with iteration and mesh | **useless for spacer comparison** |

**Judge grid / mesh convergence on `cm_avg` (and supporting \(c_b\), LMH, ΔP), not on canonical CP.** Canonical CP remains the campaign *comparison* metric once a mesh is fixed; it is a poor *grid-independence* indicator because facet artifacts move it without reflecting global resolution.

## Averaging order

**Compute \(M\) per face, then area-weight the \(M\) values.**

Never form area averages of \(c_m\) and \(c_p\) and then take their ratio.
Ratio-of-averages differs from average-of-ratios wherever the denominator is
face-dependent. `CP_max` is only meaningful pointwise (facet maximum over
window faces).

**Exception — L1:** because \(c_b\) is face-independent within a cell segment,
average-of-ratios and ratio-of-averages coincide exactly for L1.

UDM-9 already implements average-of-ratios at the UDF level (per-face CP,
then area-weighted into cells). Post-processing preserves that order.

## Canonical scalar rescaling

UDM-9 stores, per face, the L2 form with inlet denominator, area-weighted to
cells. The canonical form replaces \(c_0\) with \(c_b\). Per face the exact
conversion factor is \((c_0 - c_p)/(c_b - c_p)\). True per-face \(c_m\) /
\(c_p\) are not readable at post (Fluent 25.1 cannot consume `F_UDMI`; the
UDF area-weights face values into `C_UDMI`), so post-processing uses a
**per-cell scalar**:

\[
k_N = \frac{c_0 - c_{p,\mathrm{avg},N}}{c_{b,N} - c_{p,\mathrm{avg},N}},\quad
M_{\mathrm{canon},N} = M_{\mathrm{UDM9},N}\, k_N
\]

**Guard (raises, does not warn):** before accepting the rescale,

\[
\delta = \frac{|c_0 - c_{b,N}| \cdot \mathrm{spread}(c_p)}{{(c_{b,N} - c_{p,\min})^2}}
\]

If \(\delta > 10^{-3}\), extraction raises.

**Old spread (retired):** \(\mathrm{spread}(c_p)=c_{p,\max}-c_{p,\min}\)
from `surface-facetmax` / `surface-facetmin` of film-theory \(c_p\)
(unpaired extrema of \(c_m\) and \(J_w\)). After the area-backed
facet-min hygiene step, the low end used an area-backed substitute when
facetmin had no area support; the high end remained a raw facet maximum.

**New spread (production):** unpaired film-theory bounds per evaluation
cell on the **combined** top+bottom membrane clip:

- \(c_{p,\min}\): area-backed facetmin of \(c_m\) and facetmax of \(J_w\)
  (same hygiene as before; the 0.1% lo quantiles barely move this end
  once zero-area facetmin is handled).
- \(c_{p,\max}\): \(c_m\) at 99.9% and \(J_w\) at 0.1% of membrane area,
  each resolved by bisection on a **single reused** nested iso_clip
  (deleted after every area probe — no per-iteration surface accumulation).

\[
c_{p,\max} = \frac{B\,c_{m,q_{99.9\%}}}{J_{w,q_{0.1\%}}+B},\quad
\mathrm{spread}(c_p)=c_{p,\max}-c_{p,\min}.
\]

Per-wall (`upper`/`lower`) aggregates reuse the combined-membrane
\(k_N\); they do not re-run quantile bisection. Brackets warm-start from
the previous evaluation cell within a run.

**Why not facet extrema at the high end:** CP itself is area-weighted
(`cp_udm9_avg`). A raw facetmax spread lets a \({\sim}10^{-4}\) area
region dominate \(\delta\) while leaving the averaged CP unchanged to
\({\sim}0.2\%\). Applying an area treatment at the high end makes the
error bound consistent with the quantity it validates.

This choice is **not** sized around the \(u=0.1\) entrance-buffer saturation
patch (see Known field artifacts). At the under-converged 301-iteration stop
that patch had Yi \(\ge 0.26\) wall area fraction \(1.64\times10^{-4}\) on
u0p1 cell 7; at 2000 iterations evaluation cells 5–8 have zero such wall
area. The 99.9% high quantile excludes \(10^{-3}\) of area — under one decade
of margin relative to that transient patch. Do not claim “two orders above
the patch.”

**Before / after on D2450_a45 `max085_…_peel2` (p = 6 MPa):**

| run | \(\delta_{\max}\) facet / area-backed min | \(\delta_{\max}\) 0.1%/99.9% quantile | ratio |
| --- | ---: | ---: | ---: |
| u0p1_p6M | 0.0279 | \(3.93\times10^{-4}\) | 71 |
| u0p2_p6M | \(8.07\times10^{-5}\) | \(6.42\times10^{-5}\) | 1.26 |
| u0p3_p6M | \(3.97\times10^{-5}\) | \(3.27\times10^{-5}\) | 1.21 |

Healthy runs move by 21–26% (tighter), inside a \(2\times\) revisit
threshold. u0p1 cell 7 clears the \(10^{-3}\) guard; `cm_q_hi` = 853
against facet `cm_max` = 17072.

**Area-backed facet-min substitutions (same campaign, prior step):**
`surface-facetmin` on an x-range iso_clip can return a zero-area cut
facet (UDM from a non-membrane neighbour → exactly 0). Before the
quantile spread, those minima were replaced when the area fraction below
\(\mathrm{facet\_min}\,(1+10^{-3})\) was \(<10^{-9}\). On u0p1:

| cell | cm raw → used | jw raw → used | note |
| --- | --- | --- | --- |
| 5 | 617.9 → 617.9 | \(3.81\times10^{-6}\) kept | no rejection |
| 6 | 0 → 369 | \(1.69\times10^{-7}\) → \(2.31\times10^{-6}\) | |
| 7 | 0 → 366 | \(1.91\times10^{-9}\) → \(9.30\times10^{-7}\) | \(\sim 487\times\) in jw; most of the 35× \(\delta\) drop |
| 8 | 0 → 322 | \(3.36\times10^{-6}\) kept | cm rejected only |

Summary columns still record `cm_min_raw/used`, `jw_min_raw/used`,
`cp_facet_min_rejected_cell_{N}`, plus `cm_q_hi_cell_{N}` and
`jw_q_lo_cell_{N}`. `cm_min_used` / `jw_min_used` are the area-backed
facet minima used in \(c_{p,\min}\).

**Why \(10^{-3}\):** the previous \(10^{-4}\) threshold came from
\(\partial k/\partial c_p \sim 2\times10^{-6}\), which assumes
\(|c_0 - c_b| \sim 0.7\) mol/m³. Measured window values at \(p=6\) MPa are
7.1 (\(u=0.3\)), 12.7 (\(u=0.2\)), and \(\sim 29\) (\(u=0.1\)), giving
\(\partial k/\partial c_p\) of roughly \(2\times10^{-5}\), \(3.5\times10^{-5}\),
and \(8\times10^{-5}\) — the 2e-6 premise does not hold anywhere in the
matrix. \(\delta\) bounds the relative error on \(M\) at about \(\delta\)
itself. Campaign accuracy is set by CP discriminability within the Diamond
family (\(\sim 0.6\%\)); \(10^{-3}\) sits a factor of 6 below that. With
the quantile spread, observed \(\delta_{\max}\) at \(u=0.1\), \(p=6\) MPa
is \({\sim}4\times10^{-4}\); \(10^{-3}\) leaves margin without approaching
the discriminability floor.

Every successful extract writes `cp_canon_rescale_delta_max`,
`cp_canon_rescale_delta_status`, `cp_scalar_rescale_guard_threshold`, and
per-cell `pp_cp_canon_rescale_delta_cell_{N}` into `summary_metrics_wide.csv`
(and `raw_report_values.json`).

**Spread default (post_config `compute_cp_spread`, default False):** after the
mid-plane fix, observed \(\delta\) sits at \({\sim}10^{-5}\) against the
\(10^{-3}\) threshold (two orders of headroom). Turning spread off changes
**no** reported campaign value — e.g. `cp_canon_window_avg` =
`1.0785295947595346` either way. Measured extract-time saving was only
**9%**, so the default is about removing a guard fed by contaminated facet
minima, not about speed. With the flag off, \(k_N\) is still computed and
applied; `cp_canon_rescale_delta_max` and per-cell delta are **null** with
`cp_canon_rescale_delta_status=not_evaluated` so a missing delta is never
read as a passing delta. Set `compute_cp_spread=True` when reporting final
mesh-study numbers that need the bound.

When the flag is on, `cp_canon_rescale_delta_status=evaluated` and delta
columns are numeric as before.

**Canonical `CP_max`:** the window facet maximum uses the same per-cell scalar
\(k_N\) as the average path:

\[
M_{\mathrm{canon,max},N} = M_{\mathrm{UDM9,max},N}\, k_N
\]

This is valid only because \(k_N\) is treated as face-independent within a cell,
which is exactly what the \(\delta\) guard enforces. The guard therefore covers
the max path as well as the average — do not re-litigate max separately.

## Iteration-robust QoIs

For spacer comparison, use **`cp_canon_window_avg`** and
**`pressure_drop_spacer`** only. **`cp_canon_window_max` is not usable** for
that purpose: between the 301-iteration QoI stop and a 2000-iteration solve on
D2450_a45 u0p1 (p = 6 MPa) it moved \(2.73751 \rightarrow 125.245\)
(\(+4475\%\)) while `cp_canon_window_avg` moved only \(0.25\%\). The window
maximum is set by the single lowest-\(J_w\) face and keeps decreasing with
iteration count; treat it as a diagnostic extremum, not a campaign metric.

## Why \(c_b\) and not \(c_0\)

Bulk concentration rises along the channel by roughly \(2 J_w L_{\mathrm{active}}
/ (u_{\mathrm{mean}} h)\) (factor 2 for two membranes). Under fixed cell count,
\(L_{\mathrm{active}}\) differs between geometries, so a \(c_0\) denominator
introduces a between-geometry spread that scales with domain length rather than
spacer performance. A \(c_b\) denominator removes that spread at the definition
level.

## Evaluation window

CP is reported over the **evaluation window** (mesh manifest `n_lead_excluded`,
`n_trail_excluded`), not all active cells. Entrance effects reach +27% excess
in the first active cell and settle to about 1% by cells 4–7 on D2450_a45;
all-active averaging mixes entrance-dominated cells into the representative
value.

Legacy whole-domain `c_bulk_center_area_avg` (mid-plane over the full \(x\) span
including inlet buffers) is still written to reports for **inventory diagnostics
only** (`case_inventory.py` column
`c_bulk_center_whole_domain_area_avg`). It has **no CP consumer** — PyEnSight CP
contours use canonical `CP_WALL_CANON = udm-9 \times k_{\mathrm{window}}`, where
\(k_{\mathrm{window}}\) is the area-weighted mean of per-cell \(k_N\) over
evaluation-window cells (membrane contours are not cell-resolved). Do not
substitute `c_bulk_center_area_avg` for \(c_b\).

## Grid noise

Every published CP number should be reported alongside its grid-noise estimate
(residual grid dependence and campaign noise floor). Extraction emits window and
all-active aggregates so sensitivity to window choice can be read directly.

## Manifest

Run manifests record `analytic_cwall` parsed from the case-local dated UDF.
CP extraction requires `RO_ANALYTIC_CWALL = 1`. Report extraction also writes
post-hoc `convergence_quality` / `needs_longer_solve` from
`|lmh_relative_difference| < 1e-3`, `|mass_balance_relative_error| < 1e-3`,
and `continuity_final < 1e-4`. That gate is independent of `stop_reason`.
`pp_pressure_drop_rel_spread_window` is a WARNING, never a failure; see
**Evaluation-window dP spread** below for how to read the value.
`pp_pressure_drop_rel_spread_cells_4_7` is kept as a continuity column for
already-extracted runs.

## Known field artifacts

At \(u = 0.1\) m/s the remaining near-saturation patch is an **entrance-buffer
feature**, not a filament–membrane contact band inside an evaluation cell.
At 2000 iterations (QoI stop disabled), evaluation cells 5–8 have **zero**
wall area above \(Y_i = 0.26\) (cell 7 had wall area fraction
\(1.64\times 10^{-4}\) at the 301-iteration stop). The leftover region sits at
\(x = 0.003515\)–\(0.003731\,\mathrm{m}\), i.e. \(50\)–\(266\,\mu\mathrm{m}\)
past the buffer end at \(0.003465\,\mathrm{m}\), spanning the full spanwise
width (\(\Delta y = 3.42\,\mathrm{mm}\)) along the first filament.
`n_lead_excluded = 3` excludes it from the CP window. \(Y_{i,\max}\) fell from
\(1.0\) (clamped, 3 cells) at 301 iterations to \(0.999474\) (1 cell) at 2000.
Earlier notes that placed the saturation region at the contact band inside
cell 7 describe the under-converged 301-iteration field, not the converged
solution. Absent at \(u = 0.2\) and \(u = 0.3\), where no wall area exceeds
even \(Y_i = 0.05\).
`probe_cp_reconstruction` gives CP raw \(1.06505\) vs reconstructed
\(1.08708\), the documented \(\sim 2\%\) lift with no sign of divergence.

---

## `u_mean_ms` is mislabeled

The UDF applies \(u = U_{\mathrm{TARGET}}\times\mathrm{shape}(\eta)/G\), so
the discrete area-weighted mean inlet velocity is exactly \(U_{\mathrm{TARGET}}\).
A probe on D2450_a45 confirms \(0.2\,\mathrm{m/s}\) and inlet area
\(2.66805\times10^{-6}\,\mathrm{m}^2\) with zero clamped faces.

The run manifest records `u_mean_ms = u_target / G`. That is the
pre-normalisation profile coefficient carried over from the 260810 UDF's
`#define U_MEAN`, not the physical bulk velocity. The documented D2450_a45
reference "u_mean 0.1992807169514518" is therefore mislabeled: the physical
bulk velocity for that run is \(0.2\,\mathrm{m/s}\). Nothing in the solver
or post pipeline uses `u_mean_ms` for physics, so no results are wrong, but
do not call it \(u_{\mathrm{mean}}\). Suggested rename:
`u_profile_coefficient_ms` (not done; record the meaning until a schema
change).

## `inlet_profile_G` is a campaign constant

`REF_empty` \(G = 1.003612623\) versus Diamond D2450_a45 \(G = 1.00360939613\):
agreement of \(3.2\times10^{-6}\) between a 628k-cell empty channel and a
796k-cell spacer channel. \(G\) is the Jensen excess from discretely
integrating the concave shape function \(6\eta(1-\eta)\), not a mesh
property. The inlet face is spacer-free in every family because the buffer
regions are empty, so per-mesh \(G\) re-measurement is not physically
required. The solver still lazy-fills and compares at \(10^{-6}\) relative
as an integrity check.

## Evaluation-window dP spread

`pp_pressure_drop_rel_spread_window` is a WARNING, never a failure. It is
\((\max-\min)/|\mathrm{mean}|\) over
`evaluation_window.evaluation_cell_numbers(layout)`: global cells 5–8 on a
1+7+2 layout, 5–22 on 1+21+2. The evaluation-window mean is only
meaningful when this spread is small. No numeric fail threshold yet.

`pp_pressure_drop_rel_spread_cells_4_7` is a continuity column for runs
already on disk. On 1+7+2 it includes excluded cell 4 and omits window
cell 8. The two disagree in the sign of the error between operating
points on D2450_a45 (measured: \(u=0.2\) cells 4–7 2.56% vs window 1.79%;
\(u=0.3\) 14.75% vs 15.7%), so 4–7 cannot be scaled onto the window.
Existing runs can be re-extracted; this diagnostic does not change solver
output.

The **absolute** spread is not trustworthy to better than about \(\pm 40\%\).
At identical physics (D2450_a45, `u0p2_p6M`, quality PASS) the continuity
column `cells_4_7` spans **1.90%–3.59% (1.89×)** across the grid-study
meshes. The under-converged `max045` leaf (10.34%, FAIL, stopped at 301)
is excluded from that range.

| mesh_id | bl | m_max | m_min | cells | spread |
|---------|---:|------:|------:|------:|-------:|
| `max085_min003_cpg5_bl4_peel2` | 4 | 0.085 | 0.003 | 803,653 | 1.90% |
| `max085_min006_cpg5_bl4_peel2` | 4 | 0.085 | 0.006 | 796,009 | 2.58% |
| `max085_min006_cpg5_bl4_f040_peel2` | 4 | 0.085 | 0.006 | (inert \(f\)) | 2.58% |
| `max085_min006_cpg5_bl10_peel2` | 10 | 0.085 | 0.006 | 1,162,761 | 2.97% |
| `max085_min006_cpg5_bl6_f040_peel2` | 6 | 0.085 | 0.006 | 913,162 | 3.05% |
| `max085_min006_cpg5_bl8_peel2` (+ `_f020`, `_f040`) | 8 | 0.085 | 0.006 | 1,050,560 | 3.59% |
| `max045_min006_cpg5_bl4_peel2` | 4 | 0.045 | 0.006 | 5,146,805 | 10.34% FAIL |

`_fNNN` is inert (byte-identical physics). All of the above are `cpg5`.
The `m_min` 0.003 point is a **single** observation. Meshes for `min004`,
`max060`, `max035`, and `cpg7` exist but were never solved.

It does **not** track bl monotonically (2.58 / 3.05 / 3.59 / 2.97% at
bl 4/6/8/10). Campaign bl4 is the *lowest* of the bl series, so this is
not the same near-wall limitation as the CP grid gap (bl4 vs bl6 at fixed
`max085`, opposite direction). The spread is **not grid-converged**;
14.75% at \(u=0.3\) cannot be quoted as a physical number. Finer `m_min`
(0.003 vs 0.006) *lowers* spread 26% (1.90 vs 2.58%). Cell count is not
the driver (`min003` ≈ same cells as campaign, lower spread; bl8 +32%
cells, highest PASS spread).

The table and Re-trend numbers below are the continuity `cells_4_7`
column, not `pp_pressure_drop_rel_spread_window`.

Campaign-mesh three-point trend (`max085_min006_cpg5_bl4_peel2`), with
filament \(\mathrm{Re}_d=\rho U d/\mu\) at \(d=0.4\,\mathrm{mm}\) (all
Diamond pitches):

| \(u\) (m/s) | Re\(_{D_h}\) | Re\(_d\) | spread |
|------------:|-------------:|---------:|-------:|
| 0.1 | 172 | 44.7 | 0.24% |
| 0.2 | 344 | 89.4 | 2.58% |
| 0.3 | 516 | 134.2 | 14.75% |

Exponents in \(u\): \(0.24\to 2.58\) is \(n=3.4\); \(2.58\to 14.75\) is
\(n=4.3\). The Re trend is still real: 14.75% is 5.7× the campaign 2.58%,
well outside the 1.89× mesh band. `u0p3_p6M` (FAIL, 301) and
`u0p3_p6M_conv2000` (PASS) agree (14.76 vs 14.74%).

**Suggestive, not established:** free-cylinder shedding onset is
\(\mathrm{Re}_d \approx 47\). At \(u=0.1\) the campaign sits just below
that (\(\mathrm{Re}_d=44.7\)) with spread 0.24%, below the `REF_empty`
floor. Confinement delays shedding, and steady residual-converged data
cannot distinguish an attached bubble from suppressed shedding.

`REF_empty` on the same campaign mesh is **0.53% at both \(u=0.2\) and
\(u=0.3\)**: a plane-placement discretisation floor with no velocity
dependence. The Diamond growth is spacer-induced.

## Fixed `n_lead_excluded=3` residual bias (Diamond)

`n_lead_excluded` stays 3 on all nine Diamond geometries. Cell count
predicts entrance length better than millimetres, but is not invariant.
Archive D0817_a45_21c at \(u=0.2\): plateau from the 5th active cell
(5.8 mm); 4th active still +2.1%. D2450_a45: plateau from the 4th active
(10.4 mm); 4th active +0.6%. Millimetres to plateau 1.8×; cell counts
4 vs 3 is 1.33×.

With lead=3 the 4th active cell is the first scored cell (global 5).
One leftover cell at excess \(\varepsilon\) biases the window mean by
about \(\varepsilon/N_{\mathrm{window}}\).

| geo_id | \(n_{\mathrm{active}}\) | pitch (mm) | window | leftover at lead=3 | window-mean bias |
|--------|------------------------:|-----------:|-------:|-------------------:|-----------------:|
| D2450_a45 | 7 | 3.465 | 4 (5–8) | 0 (4th active +0.6%, already plateau) | ~0 |
| D0817_a45 | 21 | 1.155 | 18 (5–22) | 1 cell at +2.1% | **0.12%** |
| D0817_a30 | 27 | 0.943 | 24 (5–28) | 1 cell if same +2.1%; 2 cells if 3rd active (+5.7%) also leftover | 0.09% / **~0.3%** bound |
| D0817_a60 | 15 | 1.633 | 12 (5–16) | ~1 (pitch between the two anchors) | ~0.1–0.2% |
| D1225_a30 | 18 | 1.415 | 15 (5–19) | ~1 | ~0.1–0.15% |
| D1225_a45 | 14 | 1.733 | 11 (5–15) | ~1 | ~0.15% |
| D1225_a60 | 10 | 2.450 | 7 (5–11) | ~0–1 | \(\lesssim 0.3\%\) |
| D2450_a30 | 9 | 2.829 | 6 (5–10) | ~0 | ~0 |
| D2450_a60 | 5 | 4.900 | **2 (5–6)** | 0 expected | **sample size, not contamination** |

The real problem is D2450_a60: \(n_{\mathrm{active}}=5\) leaves a
two-cell window. Even a clean 4th active cell is a two-sample mean of a
periodic quantity.

## `REF_empty` discretisation excess over plane Poiseuille

Fully developed plane Poiseuille between infinite plates is
\(\mathrm{d}p/\mathrm{d}x = 12\mu U/h^2\) with campaign
\(\mu = 8.93\times10^{-4}\,\mathrm{Pa\,s}\) and \(h = 0.770\,\mathrm{mm}\):
\(3614.8\,\mathrm{Pa/m}\) at \(u=0.2\) and \(5422.1\,\mathrm{Pa/m}\) at
\(u=0.3\) (ratio exactly 1.5). Measured `pressure_drop_spacer_per_m` on
`REF_empty` is \(3707.5\) and \(5634.2\,\mathrm{Pa/m}\). The discretisation
excess over that analytic is **\(+2.6\%\) at \(u=0.2\)** and **\(+3.9\%\) at
\(u=0.3\)** (measured ratio \(1.5197\)). Both runs are `residual_converged`.
Permeation and inertia are both ruled out as causes at \(\sim 0.1\%\)
(wrong sign for permeation; Re-independent relative contribution for
inertia). Cause unexplained. Cross-velocity dP comparisons therefore
carry roughly \(1\%\) systematic; same-velocity cross-family comparisons
do not. Spanwise boundaries are periodic, so the rectangular-duct
side-wall correction does not apply.

## `membrane_blocked_area_frac` stays 0.0

Measured: `area_mem` is \(1.680871\times10^{-4}\) on `REF_empty` and
\(1.576603\times10^{-4}\) on Diamond, so Fluent's surface-area report already
excludes the spacer-membrane contact patches (6.2% on Diamond). Applying
\((1-f)\) on top would double-count. The old Pillar values 0.08 / 0.06 / 0.05
would have inflated LMH by 8.7 / 6.4 / 5.3% and were removed before any
Pillar solver run (`e493975`). `membrane_blocked_area_frac_geometric`
(Pillar 0.047 / 0.084 / 0.131) is CAD footprint metadata over the unit-cell
node, a different quantity, and is not consumed by LMH.
