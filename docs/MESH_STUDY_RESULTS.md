# Mesh-study results (recorded 2026-10-19)

Family-wise mesh tests after the 2026-10-08 supervisor meeting. This file
does not replace `docs/MESH_LANDSCAPE.md`. That file keeps the D2450_a45
parameter landscape, the 2026-10-04 screening, and the 2026-10-07..08
paste. Numbers below that also appear there are the same measurements
unless a row says otherwise.

**This WSL checkout cannot read `C:/ro_data_mfbo`.** Every QoI in the
tables is **from host report 2026-10-19**. Re-read the named leaf's
`post/reports/summary_metrics_wide.csv` on the Windows host before quoting
a digit the host report rounded. Mesh ids are what
`format_production_mesh_id` builds (`src/ro/campaign_matrix.py:66–84`).
A spacer layer count that differs from the membrane count is
`bl{membrane}s{spacer}`.

Run id is `u0p2_p6M` unless a row names another. The evaluation window is
the per-geometry table (`configs/evaluation_window_table.json`,
`docs/EVALUATION_WINDOW.md`; neither file is in this checkout yet). CP
excess is `cpc_window_avg_flux − 1` (`src/ro/cp_concentration_stats.py:226–228`,
`src/ro/extract_profile.py:74`). The supervisor's bar is a mesh-to-mesh
change within about 5–7%.

Data roots:

- `C:/ro_data_mfbo/meshstudy` — knob series
- `C:/ro_data_mfbo/campaign_bl12s4` — 27 pillar runs (nine geos × three
  p6M speeds at `max085_min006_cpg5_bl12s4_peel2`; 9 × 3 = 27)
- production copies of `bl4` may still live under `C:/ro_data`, because
  `scripts/mfbo/mesh_study_case.py` refuses a study `mesh_id` equal to
  production

Study leaf shape:

```
C:/ro_data_mfbo/meshstudy/runs/{family}/{geo_id}/{mesh_id}/u0p2_p6M/
```

---

## Pillar

CP excess is sensitive to membrane boundary-layer count. Cell size
(`m_max`) is not, on the series below. Proposed mesh:
`max085_min006_cpg5_bl12s4_peel2` (`m_max` 0.085 mm, `m_min` 0.006 mm,
`m_cpg` 5, membrane BL 12, spacer BL 4, peel 2). That id is a proposal,
not a change to `src/ro/campaign_matrix.py:58–59`.

### Membrane BL, spacer held at 4

| geo_id | mesh_id | CP excess |
|---|---|---:|
| P_p80_h15 | `max085_min006_cpg5_bl4_peel2` | 0.0351 |
| P_p80_h15 | `max085_min006_cpg5_bl8s4_peel2` | 0.0376 |
| P_p80_h15 | `max085_min006_cpg5_bl12s4_peel2` | 0.0390 |
| P_p80_h15 | `max085_min006_cpg5_bl16s4_peel2` | 0.0396 |
| P_p100_h30 | `max085_min006_cpg5_bl8s4_peel2` | 0.0356 |
| P_p100_h30 | `max085_min006_cpg5_bl12s4_peel2` | 0.0370 |
| P_p100_h30 | `max085_min006_cpg5_bl16s4_peel2` | 0.0373 |
| P_p60_h30 | `max085_min006_cpg5_bl4_peel2` | 0.0486 |
| P_p60_h30 | `max085_min006_cpg5_bl8_peel2` | 0.0479 |
| P_p60_h30 | `max085_min006_cpg5_bl8s4_peel2` | 0.0495 |
| P_p60_h30 | `max085_min006_cpg5_bl12s4_peel2` | 0.0504 |

The 2026-10-08 paste printed P_p60_h30 uniform `bl8` as 0.0480
(`docs/MESH_LANDSCAPE.md`). This host report prints 0.0479. P_p100_h30
`bl4` (0.0327 in that paste) is not in this report.

`P_p80_h15` bl12s4 → bl16s4 is 0.0390 → 0.0396 (about 1.5%), inside the
5–7% bar. `P_p100_h30` on the same step is 0.0370 → 0.0373.

### Cell size at BL 12s4 (`P_p80_h15`)

Membrane 12 / spacer 4. `m_min` 0.006, `m_cpg` 5, peel 2.

| mesh_id | cells | CP excess |
|---|---:|---:|
| `max085_min006_cpg5_bl12s4_peel2` | 1.6 M | 0.0390 |
| `max045_min006_cpg5_bl12s4_peel2` | 5.7 M | 0.0391 |
| `max035_min006_cpg5_bl12s4_peel2` | 7.3 M | 0.0399 |
| `max025_min006_cpg5_bl12s4_peel2` | 42.6 M | 0.0391 |

42.6 M cells is 4.3 M per unit cell at `m_max` 0.025. LMH within 0.1%,
dP within 1.8% on this series. The host report did not name the LMH or
dP columns; the comparison columns in extract are `lmh_window_module`
and `pressure_drop_spacer_per_m` (`src/ro/extract_profile.py:65`, `:73`).

### Other knobs (`P_p80_h15`)

`m_min` 0.006 → 0.004: CP excess −1.7%. Mesh id
`max085_min004_cpg5_bl4_peel2` unless the host held BL 12s4; the report
did not repeat the other knobs. The 2026-10-04 screening called this
knob negligible on production `bl4` (`docs/MESH_LANDSCAPE.md`).

---

## ML (`M_c267`)

Family directory `ml`. Proposed: BL 10
(`max085_min006_cpg5_bl10_peel2`). Not a campaign-matrix change.

| knob | mesh_id | CP excess |
|---|---|---:|
| `m_max` 0.085 | `max085_min006_cpg5_bl4_peel2` | 0.0583 |
| `m_max` 0.060 | `max060_min006_cpg5_bl4_peel2` | 0.0579 |
| `m_max` 0.045 | `max045_min006_cpg5_bl4_peel2` | 0.0591 |
| BL 4 | `max085_min006_cpg5_bl4_peel2` | 0.0583 |
| BL 8 | `max085_min006_cpg5_bl8_peel2` | 0.0638 |
| BL 10 | `max085_min006_cpg5_bl10_peel2` | 0.0647 |

Cell size is inside the 5–7% bar. Membrane BL 4 → 10 is 0.0583 → 0.0647
(about +11%). The 2026-10-08 paste had BL 8 as +9.4% versus `bl4` and
`m_max` 0.060 as −0.7%; those relatives match 0.0638 / 0.0583 and
0.0579 / 0.0583.

### BL 10 campaign

**From host report 2026-10-28.** Nine runs: three geos (`M_c160`,
`M_c267`, `M_c400`) × three p6M speeds at
`max085_min006_cpg5_bl10_peel2`. All PASS. The `bl4` study jobs were
refused by design (`scripts/mfbo/mesh_study_case.py` will not queue a
study `mesh_id` equal to production). BL 4 numbers are
`campaign_reextract_v2`.

CP excess versus production BL 4 is +6–11% except `M_c160`: +20% at
`u0p2` and +61% at `u0p3`. dP (`pressure_drop_spacer_per_m`) and LMH
(`lmh_window_module`) stay within 2.4%. The BL-10 proposal above is
unchanged and is still not a campaign-matrix change.

---

## Diamond

Not converged. The last `m_max` step changes CP itself by less than 1%;
dP and LMH stay within about 1–5%. The series as a whole still moves
CP excess with cell size. Decision **OPEN**: contact-geometry change,
exclusion band, or report with uncertainty.

**2026-10-22.** A last-ratio series at fixed `m_max` 0.085 holds the
in-plane mesh and changes the membrane first height. CP excess moves
0.0595 → 0.0671 from realised y1 5.10 µm to 1.32 µm. The
smooth-transition `m_max` rows at a similar y1 are much higher, so most
of that `m_max` dependence is in-plane surface and bulk resolution, not
y1. **2026-10-23.** The fixed-y1 series at request 2.0 µm (realised
1.84–1.89 µm) still climbs 0.0663 → 0.0940; the last step is +2.0%.
The smooth-transition `m_max` 0.028 leaf remains 17% below that
plateau. Decision remains **OPEN**.

### `m_max` at `bl4`

| geo_id | mesh_id | CP excess | note |
|---|---|---:|---|
| D0817_a30 | `max085_min006_cpg5_bl4_peel2` | 0.0797 | |
| D0817_a30 | `max060_min006_cpg5_bl4_peel2` | 0.0996 | |
| D0817_a30 | `max045_min006_cpg5_bl4_peel2` | 0.1197 | |
| D0817_a30 | `max035_min006_cpg5_bl4_peel2` | 0.1294 | |
| D0817_a30 | `max025_min006_cpg5_bl4_peel2` | 0.1404 | gate FAIL |
| D2450_a45 | `max085_min006_cpg5_bl4_peel2` | 0.0583 | |
| D2450_a45 | `max060_min006_cpg5_bl4_peel2` | 0.0705 | |
| D2450_a45 | `max045_min006_cpg5_bl4_peel2` | 0.0827 | |
| D2450_a45 | `max035_min006_cpg5_bl4_peel2` | 0.0907 | |

### Last-ratio membrane y1 (`D2450_a45`, `m_max` 0.085)

**From host report 2026-10-22.** `u0p2_p6M`. Last-ratio membrane BL,
spacer still smooth-transition, `bl` 4. CP excess is
`cpc_window_avg_flux − 1`. The step column is the change from the row
above. The film column is the host-reported relative correction from
the raw first-cell window mean `c1_area_mean_window` to the
reconstructed `cm_area_mean_window`. dP (`pressure_drop_spacer_per_m`)
and LMH (`lmh_window_module`) stay within 1% on this series. Requested
y1 1.0 µm (`_y1h010`) is absent: that mesh failed the quality gate
(max aspect ratio 178.6 > 150).

| request [µm] | mesh_id | realised y1 [µm] | CP excess | step | c1 → cm |
|---:|---|---:|---:|---:|---:|
| 5.7 | `max085_min006_cpg5_bl4_peel2_y1h057` | 5.10 | 0.0595 | | 1.9% |
| 3.0 | `max085_min006_cpg5_bl4_peel2_y1h030` | 2.71 | 0.0653 | +9.7% | 1.0% |
| 2.0 | `max085_min006_cpg5_bl4_peel2_y1h020` | 1.84 | 0.0663 | +1.5% | 0.7% |
| 1.4 | `max085_min006_cpg5_bl4_peel2_y1h014` | 1.32 | 0.0671 | +1.2% | 0.5% |

The smooth-transition baseline at the same `m_max` is
`max085_min006_cpg5_bl4_peel2`, realised y1 5.74 µm, CP excess 0.0583.
The last-ratio control (realised 5.10 µm, CP excess 0.0595) is
(0.0595 − 0.0583) / 0.0583 ≈ 2%. The switch of offset method, at
matched y1, moves CP by about 2%. The later steps, 2.71 → 1.32 µm, are
+1.5% and +1.2%, inside the 5–7% bar. The 5.10 → 2.71 µm step is
+9.7%.

At a similar realised y1 the smooth-transition `m_max` series is higher.
`m_max` 0.035 (`max035_min006_cpg5_bl4_peel2`, y1 2.60 µm) is 0.0907,
against 0.0653 here at y1 2.71 µm. `m_max` 0.028
(`max028_min006_cpg5_bl4_peel2`, y1 2.11 µm) is 0.0784; that row was
not in the 2026-10-19 table above. Both sit well above the last-ratio
values at `m_max` 0.085 and a similar y1. The `m_max` trend is in-plane
surface and bulk resolution.

### Fixed y1 2.0 µm, `m_max` sweep

**From host report 2026-10-23.** `u0p2_p6M`. Same last-ratio path as the
y1 series, request `_y1h020`, spacer still smooth-transition, `bl` 4.
Realised y1 is 1.84–1.89 µm. CP excess is `cpc_window_avg_flux − 1`.
The step column is the change from the row above. On the last step
(`m_max` 0.045 → 0.035), `cp_q999_window_flux − 1` is +1.2%,
`lmh_window_module` is −0.2%, and `pressure_drop_spacer_per_m` is +0.3%.
Layer count is the last-ratio stack at that `m_max`. The 0.085 leaf is
the 2.0 µm row above and is not requeued.

| m_max [mm] | mesh_id | layers | cells | CP excess | step |
|---:|---|---:|---:|---:|---:|
| 0.085 | `max085_min006_cpg5_bl4_peel2_y1h020` | 8 | 0.87 M | 0.0663 | |
| 0.060 | `max060_min006_cpg5_bl4_peel2_y1h020` | 7 | 4.30 M | 0.0749 | +13% |
| 0.045 | `max045_min006_cpg5_bl4_peel2_y1h020` | 6 | 5.29 M | 0.0922 | +23% |
| 0.035 | `max035_min006_cpg5_bl4_peel2_y1h020` | 5 | 6.29 M | 0.0940 | +2.0% |
| 0.028 | `max028_min006_cpg5_bl4_peel2_y1h020` | 4 | 41.3 M | 0.0790 | −16% |

The 0.035 step is inside the 5–7% bar. The series then falls,
0.0940 → 0.0790, about −16%, at 41.3 M cells.

This fixed-y1 `m_max` series is confounded, so that drop cannot be
attributed to bulk refinement. Poly-hexcore hex edges on this domain
snap to 34.65 / 2^n mm. Two of those steps sit in the table: 0.085 →
0.060 crosses 0.0677 mm (512), and 0.035 → 0.028 crosses 0.0338 mm
(1024). The rows at 0.060, 0.045, and 0.035 share the 0.0338 mm edge,
so their cell counts move only with the shell. The membrane stack also
changes with `m_max`: 8, 7, 6, 5, then 4 layers at 0.028. The mesh id
stays `bl4` because that token is the smooth-transition baseline. The
0.028 drop mixes the second octree step, the surface `m_max` change,
and a 4-layer stack against the 5-layer stack at 0.035.

The bulk-only series holds surface `m_max` at 0.035 and y1 at 2.0 µm
(5 membrane layers on every leaf) and changes only the polyhedra max
cell length. `scripts/mfbo/d2450_bulk_queue.py` emits `bms034`,
`bms026`, and `bms020`
(`max035_min006_cpg5_bl4_peel2_y1h020_bms034` and the same stem with
`_bms026` and `_bms020`). Mesh with `--mesh-only` first. Each log prints
the cell count and `membrane_bl_layers`, which must be 5 on all three.
The emitter prints expected cell counts 6,300,000, 12,000,000, and
25,000,000. Then run the full queue. Each job passes `--reuse-mesh`
and `--checkpoint-every 200`.

The smooth-transition `m_max` 0.028 leaf
(`max028_min006_cpg5_bl4_peel2`, 41.25 M cells, realised y1 2.11 µm,
CP excess 0.0784) is a different mesh. 41.25 M is 6.7× the
smooth-transition `m_max` 0.035 count (6,154,123,
`docs/MESH_LANDSCAPE.md`). That jump is the same 0.0338 mm octree step,
with surface `m_max` changing as well.
`scripts/mfbo/d2450_mmax_y1_queue.py --m-max 0.028` still emits the
fixed-y1 0.028 leaf above. It is the confounded point, not the bulk
series. The default m_max list stays 0.060, 0.045, and 0.035.

### Contact proximity (`D0817_a30`, `m_max` 0.045)

**From host report 2026-10-22.** Base is the `m_max` 0.045 row above.
Local proximity is spacer against both membrane walls
(`scripts/mfbo/d0817_contact_queue.py`).

| variant | mesh_id | CP excess | note |
|---|---|---:|---|
| base | `max045_min006_cpg5_bl4_peel2` | 0.1197 | |
| `lpx9m003` | `max045_min006_cpg5_bl4_peel2_lpx9m003` | 0.1181 | cpg 9, min 0.003 mm |
| `lpx13m002` | `max045_min006_cpg5_bl4_peel2_lpx13m002` | 0.1279 | cpg 13, min 0.002 mm; realised y1 2.21 µm |

### Other knobs (`D0817_a30`)

| knob | mesh_id | CP excess | note |
|---|---|---:|---|
| `m_cpg` 7 | `max085_min006_cpg7_bl4_peel2` | 0.0956 | +20% vs 0.0797 |
| `m_min` 0.004 | `max085_min004_cpg5_bl4_peel2` | 0.0803 | |
| BL 8 | `max085_min006_cpg5_bl8_peel2` | 0.0819 | |
| split BL 8s4 | `max085_min006_cpg5_bl8s4_peel2` | 0.0638 | anomalous |

**Interpretation, not a measurement.** The Diamond `m_max` / `m_cpg`
move is resolution of the filament–membrane contact wedge. Split BL 8s4
at 0.0638 sits below the `bl4` value 0.0797; the 2026-10-04 screening
already called split BL unsafe for contact geometries
(`docs/MESH_LANDSCAPE.md`). The sinusoidal test below is
`S_a144_l3465`, not a family-wide series.

---

## Empty (`REF_empty`)

**From host report 2026-10-28.** `u0p2_p6M`. Production baseline is
`campaign_reextract_v2` at `max085_min006_cpg5_bl4_peel2` (`m_max`
0.085). Study leaves are under `C:/ro_data_mfbo/meshstudy`. The `bl4`
study jobs were refused by design (study `mesh_id` = production id).
CP excess is `cpc_window_avg_flux − 1`. dP is
`pressure_drop_spacer_per_m`. LMH is `lmh_window_module`. The step
column is versus that BL 4 baseline.

Baseline: CP excess 0.1551, `cp_q999_window_flux` 1.1683, dP 3707,
LMH 23.324.

| knob | mesh_id | cells | y1 [µm] | CP excess | step | dP [Pa/m] | LMH |
|---|---|---:|---:|---:|---:|---:|---:|
| BL 8 | `max085_min006_cpg5_bl8_peel2` | 0.76 M | 3.25 | 0.1569 | +1.2% | 3684 | 23.275 |
| BL 12 | `max085_min006_cpg5_bl12_peel2` | 0.84 M | 1.57 | 0.1568 | +1.1% | 3704 | 23.278 |
| `m_max` 0.060, BL 4 | `max060_min006_cpg5_bl4_peel2` | 4.83 M | 4.71 | 0.1564 | +0.8% | 3628 | 23.290 |

No contact. BL 4 is already inside the 5–7% bar on CP, dP, and LMH.
Spacer-family CP sensitivity is a contact / in-plane effect, not
membrane-BL count or \(c_m\) reconstruction.

---

## Sinusoidal (`S_a144_l3465`)

**From host report 2026-10-28.** `u0p2_p6M`. Family directory `sin`.
Production baseline is `campaign_reextract_v2` at
`max085_min006_cpg5_bl4_peel2`. Study leaves are under
`C:/ro_data_mfbo/meshstudy`. The `bl4` study jobs were refused by
design. CP excess is `cpc_window_avg_flux − 1`. The step column is
versus the row above for the BL and `m_max` ladders separately (BL 8
versus baseline; BL 10 versus BL 8; `m_max` 0.060 versus baseline;
`m_max` 0.045 versus 0.060).

Baseline: CP excess 0.0972, `cp_q999_window_flux` 1.8225, dP 37869,
LMH 23.256.

| knob | mesh_id | cells | CP excess | step |
|---|---|---:|---:|---:|
| BL 8 | `max085_min006_cpg5_bl8_peel2` | 3.80 M | 0.0990 | +1.9% |
| BL 10 | `max085_min006_cpg5_bl10_peel2` | 4.23 M | 0.1009 | +1.9% |
| `m_max` 0.060, BL 4 | `max060_min006_cpg5_bl4_peel2` | 6.20 M | 0.1080 | +11.1% |
| `m_max` 0.045, BL 4 | `max045_min006_cpg5_bl4_peel2` | 7.80 M | 0.1103 | +2.1% |

dP within 0.4%, LMH within 1.3% on this series. Membrane BL is inside
the 5–7% bar. The `m_max` move is in-plane type and is inside the bar
on the last step (0.060 → 0.045). Bulk-level refinement (the Diamond
`m_max` 0.028 drop on `D2450_a45`) is not tested. Other sinusoidal
geos are not in this series.

---

## Non-convergence (`P_p100_h00`)

`P_p100_h00` at `u0p2` and `u0p3` still FAIL at 5.3 M cells (`m_max`
0.045, BL 12s4, mesh id `max045_min006_cpg5_bl12s4_peel2`). Not a mesh
issue on this evidence. UDF LMH (`lmh_udm_avg`) is stable. Mass-imbalance
localization is not completed (`scripts/mfbo/locate_mass_imbalance.py`).

The 2026-10-08 meeting treated \(u = 0.3\) laminar FAIL as a coarse-mesh
hypothesis (`docs/MESH_LANDSCAPE.md`). This leaf is that re-test. The
hypothesis does not hold for this geometry.

Monitor reporting when the gate fails is
`docs/metrics_conventions.md` (steady monitor, 2026-10-09).

**Hypothesis (supervisor, unverified; 2026-10-19).** Failures correlate
with \(\mathrm{Re}_D = \rho u D_p / \mu\) above about 200–270:
`P_p100` at `u0p2` / `u0p3`, `P_p80_h15` at `u0p3`. Bores of 0.30 mm
converge at the same \(\mathrm{Re}_D\). Estimate, to be confirmed.
\(\rho = 998.2\,\mathrm{kg\,m^{-3}}\), \(\mu = 8.93\times 10^{-4}\,\mathrm{Pa\,s}\)
(`configs/run_config.py:282–283`). \(D_p\) from the geo_id token
(`src/ro/campaign_geometry.py:355–360`). Transient check (pathlines,
transient run) is open (`docs/PIPELINE_MAP.md`, Status 2026-10-19).

---

## Benchmark extracts

**From host report 2026-10-19.** Per-leaf
`post/reports/summary_metrics_wide.csv` under the named roots. Columns:
`pressure_drop_spacer_per_m`, `cpc_window_avg_flux − 1`,
`cp_q999_window_flux − 1`, `lmh_window_module`, `lmh_window_exposed`
(`src/ro/extract_profile.py:65–66`, `:73–75`;
`scripts/mfbo/summarize_results.py:115–126`). Digits are as printed;
do not round further. Do not substitute the 2026-10-03
`docs/P6M_COMPARISON.md` tables (`cp_canon_window_avg` and whole-active
`lmh_mass_balance`). A one-leaf family is a single value, not a range.
`n` is leaves in that extract at that speed; `FAIL` is the gate count
on those leaves.

`campaign_reextract_v2` leaves lack the `window_source` column. That
field was added after that re-extract (commit `e9ff8cb`;
`src/ro/extract_profile.py:70`;
`src/ro/evaluation_window_table.py:65`, `:75`). They were extracted
with the per-geometry window table.

### Production mesh, BL 4 (`campaign_reextract_v2`)

Data root `C:/ro_data_mfbo/campaign_reextract_v2`. Production mesh
`max085_min006_cpg5_bl4_peel2` (`D0817_a60`
`max060_min006_cpg5_bl4_peel2`). Run id `u0p2_p6M`. The extract as a
whole is 93 runs (31 geos × three p6M speeds). This table is the
`u0p2` column.

| family | n | FAIL | dP/L [Pa/m] | CP excess | CP_q999−1 | LMH module | LMH exposed |
|---|---:|---:|---|---|---|---|---|
| diamond | 9 | 0 | 16511 – 273130 | 0.04231 – 0.1033 | 0.1439 – 0.521 | 20.09 – 24.39 | 24.68 – 26.25 |
| empty | 1 | 0 | 3707 | 0.1551 | 0.1683 | 23.32 | 23.32 |
| ml | 3 | 0 | 43397 – 92814 | 0.05158 – 0.06054 | 0.2806 – 0.3615 | 24.36 – 24.90 | 25.78 – 26.02 |
| pillar | 9 | 1 | 52157 – 93928 | 0.03102 – 0.04865 | 0.05718 – 0.09277 | 23.08 – 25.07 | 26.12 – 26.56 |
| sin | 9 | 0 | 21633 – 79052 | 0.06039 – 0.1444 | 0.6962 – 0.9683 | 22.13 – 24.00 | 23.63 – 25.79 |

### Pillar BL 12/4 (`campaign_bl12s4`)

Data root `C:/ro_data_mfbo/campaign_bl12s4`. Nine pillar geos at
`max085_min006_cpg5_bl12s4_peel2`. 27 runs (9 × 3 speeds).

| run_id | n | FAIL | dP/L [Pa/m] | CP excess | CP_q999−1 | LMH module | LMH exposed |
|---|---:|---:|---|---|---|---|---|
| `u0p1_p6M` | 9 | 0 | 19730 – 31583 | 0.05736 – 0.1104 | 0.1248 – 0.1917 | 22.34 – 23.63 | 24.49 – 25.86 |
| `u0p2_p6M` | 9 | 2 | 51845 – 94667 | 0.03367 – 0.05043 | 0.08025 – 0.1027 | 22.98 – 24.99 | 26.07 – 26.49 |
| `u0p3_p6M` | 9 | 3 | 95860 – 184656 | 0.02636 – 0.03692 | 0.06857 – 0.08187 | 23.17 – 25.24 | 26.42 – 26.70 |
