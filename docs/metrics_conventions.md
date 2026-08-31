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
- \(c_b\): mid-plane (\(z = h/2\)) **mass-weighted** (mixing-cup) salt
  concentration [mol/m³], clipped in \(x\) to each evaluation-window unit cell
  (not the whole domain). Implemented as Fluent `surface-massavg` on an
  x-range iso-clip of the mid-plane iso-surface. Do not use
  `surface-areaavg` for \(c_b\): area-weighted species averages are not
  conserved across buffer regions with no membrane source (see campaign
  probe STEP F / `_tmp_cell_profile.py`).
- \(c_0\): inlet reference concentration (`c_inlet_ref`, 597.8268309 mol/m³).

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

**New spread (production):** \(\mathrm{spread}(c_p)=c_{p,q_{\mathrm{hi}}}-c_{p,q_{\mathrm{lo}}}\)
at 99.9% / 0.1% of membrane area per evaluation cell. Resolve the
membrane-area CDF of \(c_m\) and \(J_w\) by bisection on nested
iso_clip, then form unpaired film-theory bounds

\[
c_{p,\min} = \frac{B\,c_{m,q_{0.1\%}}}{J_{w,q_{99.9\%}}+B},\quad
c_{p,\max} = \frac{B\,c_{m,q_{99.9\%}}}{J_{w,q_{0.1\%}}+B}.
\]

**Why not facet extrema:** CP itself is area-weighted
(`cp_udm9_avg`). A raw facetmax / facetmin spread lets a
\({\sim}10^{-4}\) area region dominate \(\delta\) while leaving the
averaged CP unchanged to \({\sim}0.2\%\). The low end was already
area-backed (`FACET_MIN_TARGET_AREA_FRAC` \(=10^{-4}\), the 0.01% low
quantile); the high end remained a raw facet maximum — asymmetric.
Applying the same area treatment at both ends (0.1% / 99.9%, central
99.8% of membrane area) makes the error bound consistent with the
quantity it validates.

This choice is **not** sized around the \(u=0.1\) saturation patch
(Yi \(\ge 0.26\) area fraction \(1.64\times10^{-4}\) on u0p1 cell 7). The
99.9% high quantile excludes \(10^{-3}\) of area, about \(6\times\) that
patch — under one decade of margin. Do not claim “two orders above the
patch.”

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
`cp_facet_min_rejected_cell_{N}`, plus `cm_q_lo/hi_cell_{N}` and
`jw_q_lo/hi_cell_{N}`. `cm_min_used` / `jw_min_used` are the 0.1%
quantiles used in the unpaired bound.

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
`cp_scalar_rescale_guard_threshold`, and per-cell
`pp_cp_canon_rescale_delta_cell_{N}` into `summary_metrics_wide.csv` (and
`raw_report_values.json`), not only when the guard fires.

**Canonical `CP_max`:** the window facet maximum uses the same per-cell scalar
\(k_N\) as the average path:

\[
M_{\mathrm{canon,max},N} = M_{\mathrm{UDM9,max},N}\, k_N
\]

This is valid only because \(k_N\) is treated as face-independent within a cell,
which is exactly what the \(\delta\) guard enforces. The guard therefore covers
the max path as well as the average — do not re-litigate max separately.

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
CP extraction requires `RO_ANALYTIC_CWALL = 1`.

## Known field artifacts

At \(u = 0.1\) m/s, salt accumulates in the stagnation region adjacent to the
filament-membrane contact band (within \(\sim 5\,\mu\mathrm{m}\) of the
membrane, \(\sim 100\times 80\,\mu\mathrm{m}\) footprint, inside the
\(152\,\mu\mathrm{m}\) contact flat; \(z = 0.380\)–\(0.383\,\mathrm{mm}\)
against membranes at \(z = \pm 0.385\,\mathrm{mm}\)). Enrichment above
\(Y_i = 0.05\) appears in all four evaluation cells (area fraction
\(1.1\)–\(2.1\times 10^{-3}\)); in cell 7 only it reaches NaCl saturation,
with 3 interior cells clamping at \(Y_i = 1\). Wall area fraction above
\(Y_i = 0.26\) is \(2.3\times 10^{-5}\), four orders below the \(\sim 0.6\%\)
CP discriminability signal, so window CP is unaffected. Absent at
\(u = 0.2\) and \(u = 0.3\), where no wall area exceeds even \(Y_i = 0.05\).
`probe_cp_reconstruction` gives CP raw \(1.06505\) vs reconstructed
\(1.08708\), the documented \(\sim 2\%\) lift with no sign of divergence.
Localised to one evaluation cell and not explained by geometry, which
repeats every pitch.
