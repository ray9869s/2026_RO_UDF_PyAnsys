# Mesh landscape

Parameter exploration, inventory, and convergence notes for the RO spacer
campaign. All exploration numbers below are on **D2450_a45** unless stated.
Campaign baseline mesh_id: `max085_min006_cpg5_bl4_peel2` (796,009 cells).

See also `docs/GEOMETRY_DESIGN.md` (joint spheres, Pillar bore) and
`docs/metrics_conventions.md` (coordinates, QoI reliability).

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

- **`bl_height_factor` is inert.** `bl8_f020` and `bl8_f040` have different
  `mesh_sha256` but byte-identical physics (`cp_canon` 1.036991, \(c_b\)
  622.626, `lmh` 25.6599, ΔP 843.791, \(y_1\) 2.769 µm). Smooth-transition
  offset ignores the specified first height and sets prism thickness from
  layer count and growth rate alone. The `_fNNN` mesh_id token is **retired**.
- **\(y_1\) responds to both axes and is not separable:** bl4 5.736 µm, bl6
  3.985, bl8 2.769, bl10 1.926; `m_max` 0.045 at bl4 already gives 3.304 µm.
  Smooth-transition sets prism thickness from the adjacent core cell.
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

---

## Mesh inventory (21 built; campaign mesh target 31)

21 = 9 Diamond + 3 ML + 9 Pillar are built today. The campaign mesh target is
**31** = 21 + 9 Sinusoidal + `REF_empty` (empty channel is meshed separately).
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
invalidate its verified references (D2450_a45: 796,009 cells, \(u_{\mathrm{mean}}\)
0.1992807169514518, `inlet_profile_G` 1.00360939613) — leave it.
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
## Convergence

### Post-hoc gate

Pass when all of:

- `|lmh_relative_difference| < 1e-3`
- `|mass_balance_relative_error| < 1e-3`
- `continuity_final < 1e-4`

Independent of solver `stop_reason`.

**Removed from the gate:** pressure-drop spread across cells 4–7. That spread
is converged-state physics (2.58% at \(u=0.2\), 14.7% at \(u=0.3\)), unchanged
between the 301-iteration and 765-iteration solutions. It is recorded as
`pp_pressure_drop_rel_spread_cells_4_7` **diagnostic only**.

### Independence study (D2450_a45, p = 6 MPa)

2000 iterations with QoI stop off vs the 301-iteration QoI stop:

| run | result |
|-----|--------|
| u0p1 | `max_iter_reached`; continuity floors at \(4.2\times10^{-7}\) and never reaches \(10^{-7}\); `lmh_udm_avg` fixed to 9 decimals from iteration 298 |
| u0p2 | `residual_converged` at 379; every quantity identical to 6 sig figs vs 301 stop |
| u0p3 | `residual_converged` at 765; CP −0.010%, ΔP +0.074%; `lmh_relative_difference` went \(-0.2925 \rightarrow 1.30\times10^{-4}\) |

So **`qoi_initial_values_to_ignore` does not need raising**: what the 301 stop
missed is exactly what the `lmh_relative_difference` gate catches.

### \(u = 0.1\) saturation patch

A stagnation region near the first filament exceeds NaCl saturation. At 2000
iterations it sits entirely in the entrance buffer
(\(x = 0.003515\)–\(0.003731\,\mathrm{m}\)) with **zero** wall area above
\(Y_i = 0.26\) in evaluation cells 5–8. `n_lead_excluded = 3` excludes it.
At 301 iterations it appeared in cell 7 — an **under-convergence artifact**,
not a contact-band feature of the converged field.
