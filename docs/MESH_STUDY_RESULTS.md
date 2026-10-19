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
0.0579 / 0.0583. An ML BL-10 campaign has not been run
(`docs/PIPELINE_MAP.md`, Status 2026-10-19).

---

## Diamond

Not converged. The last `m_max` step changes CP itself by less than 1%;
dP and LMH stay within about 1–5%. The series as a whole still moves
CP excess with cell size. Decision **OPEN**: contact-geometry change,
exclusion band, or report with uncertainty.

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
(`docs/MESH_LANDSCAPE.md`). A sinusoidal mesh study has not been run.

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
