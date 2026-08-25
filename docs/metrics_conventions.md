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
- \(c_b\): mid-plane (\(z = h/2\)) area-average salt concentration [mol/m³],
  clipped in \(x\) to each evaluation-window unit cell (not the whole domain).
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
conversion factor is \((c_0 - c_p)/(c_b - c_p)\); face-dependence in that
factor is negligible (\(\partial k/\partial c_p \sim 2\times10^{-6}\) mol/m³
for campaign values), so post-processing uses a **per-cell scalar**:

\[
k_N = \frac{c_0 - c_{p,\mathrm{avg},N}}{c_{b,N} - c_{p,\mathrm{avg},N}},\quad
M_{\mathrm{canon},N} = M_{\mathrm{UDM9},N}\, k_N
\]

**Guard (raises, does not warn):** before accepting the rescale,

\[
\delta = \frac{|c_0 - c_{b,N}| \cdot \mathrm{spread}(c_p)}{{(c_{b,N} - c_{p,\min})^2}}
\]

using min/max of the per-face \(c_p\) expression as the spread. If
\(\delta > 10^{-4}\) (0.01%, two orders below the CP noise floor), extraction
raises. \(\delta\) is logged in `raw_report_values.json` for every run.

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
