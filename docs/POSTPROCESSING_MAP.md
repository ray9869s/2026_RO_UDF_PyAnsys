# Post-processing map — report extraction stage

Read-only map of what `scripts/pyfluent_report_extract.py` does when
`batch_postprocess_all_cases.py` is run with `--run-reports` only
(`--no-run-pyensight-contours --no-run-shear`). Contour / shear-image stages
are covered only in §6 for contrast.

Source of truth: `scripts/pyfluent_report_extract.py`,
`src/ro/fluent_report_helpers.py`, `src/ro/cp_metrics.py`,
`src/ro/lmh_metrics.py`, `src/ro/domain_layout.py`, `src/ro/udm_layout.py`.
Where the code does not settle a point, this doc says **UNKNOWN**.

Typical layout assumed below: Diamond `1+7+2` with
`EvaluationWindow(n_lead_excluded=3, n_trail_excluded=0)` → evaluation cells
**5–8**. Active (spacer) cells are **2–8**. Unit-cell boundary planes are
indices **0…10** (11 planes).

---

## 1. Sequence of operations

Labels match the `# Cell N` banners in `pyfluent_report_extract.py`.

### Cell 1 — Import and load config

- Loads `configs/post_config.py` (or `PYFLUENT_POST_CONFIG`).
- Batch overrides arrive via `PYFLUENT_POST_OVERRIDES` (set by
  `batch_postprocess_all_cases.py` / `batch_report_extract.py`).
- No Fluent objects yet.

### Cell 2 — Paths and output folders

- Resolves `case_path`, `final.cas.h5`, `final.dat.h5` under `RO_DATA_ROOT`.
- Creates `post/` and `post/reports/`.
- Declares output paths (written later in Cell 10):
  - `summary_metrics.csv` (long)
  - `summary_metrics_wide.csv` (one row, ~164 columns)
  - `mass_balance.csv`, `pressure_report.csv`, `wall_shear_report.csv`
  - `raw_report_values.json`

### Cell 3 — Helpers (definitions only)

- Zone collectors, `create_or_update_*_report`, plane/iso helpers imported from
  `ro.fluent_report_helpers` (and local wrappers). No session work.

### Cell 4 — Launch Fluent and load case/data

1. `pyfluent.launch_fluent(..., mode="meshing", cwd=<case_path>)`.
2. `meshing.switch_to_solver()`.
3. `solver.settings.file.read_case_data(final_case_file)`.

The script does **not** call UDF compile APIs. If the Fluent log shows
libudf rebuild, that is Fluent’s own reaction to loading a case that
references a UDF — **UNKNOWN** whether it always recompiles vs reuse.

### Cell 5 — Detect zones

- Classifies BC / cell zones; selects `active_membrane_zones`
  (`wall_top_mem` / `wall_bottom_mem` base names from layout), inlets,
  outlets, fluid zones.
- Syncs run-manifest analytic wall concentration if configured.

### Cell 6 / 6.5 — Field names and layout

- Binds UDM field names from `ro.udm_layout` (e.g. `udm-6`=`Jw`, `udm-7`=`Cm`,
  `udm-8`=`LMH`, `udm-9`=`CP` / `cp_inlet`, …).
- Builds `DomainLayout` + `EvaluationWindow` from the mesh/run manifest via
  `resolve_scoring_layout_from_config`.
- Prints spacer plane x locations and evaluation cell numbers.

### Cell 7 — Create report definitions (and planes)

Creates **persistent** Fluent surfaces and report definitions (not deleted
until session exit). Order:

| Step | Fluent objects | Feeds |
| --- | --- | --- |
| Mass flow | flux reports `pp_m_in`, `pp_m_out` | LMH mass balance, mass balance |
| Membrane area | `pp_area_mem` on active membranes | LMH denominator |
| Named expressions | `pp_lmh_mass_balance`, `pp_lmh_mass_balance_signed` | LMH columns |
| Inlet/outlet pressure | `pp_p_in_avg`, `pp_p_out_avg`, expr `pp_pressure_drop` | full-domain ΔP |
| Spacer planes | iso-surfaces `pp_plane_spacer_in`, `pp_plane_spacer_out` | spacer ΔP |
| Spacer pressure | `pp_p_spacer_in/out_avg`, expr `pp_pressure_drop_spacer` | spacer ΔP |
| Unit-cell planes | `pp_plane_unit_cell_boundary_{i}` for each boundary | per-cell ΔP / salt rise |
| Per-boundary reports | pressure areaavg, salt areaavg, salt massavg, plane area | same |
| Membrane UDM / wall shear | `pp_jw_*`, `pp_cm_*`, `pp_lmh_udm_*`, `pp_cp_inlet_*`, `pp_salt_flux_*`, `pp_wall_shear_*` | whole-membrane scalars |
| Volume integrals | `pp_volint_salt_mass_source`, `pp_volint_total_mass_source`, UDM area sum | mass balance |

`membrane_blocked_area_frac` is read from the **run manifest** into the LMH
expression (not a Fluent query). It is **0.0 campaign-wide**: Fluent
`area_mem` already excludes contact patches. See
`docs/metrics_conventions.md`.

### Cell 8 — Compute Cell-7 reports

- Loops `report_names`, calls `compute_one_report` for each.
- Populates `computed_values` / `raw_results`.
- Output columns later alias many of these names (e.g. `pp_jw_avg` → `jw_avg`).

### Cell 8.25 — Salt mass-fraction range diagnostics

- Uses `solver.fields.reduction` on fluid zones (not surface reports).
- Columns: `pp_salt_mass_fraction_{cells_above,cells_below,max,min}_threshold`
  (and error strings if it fails).

### Cell 8.4 — Mid-plane bulk \(c_b\) (canonical CP denominator)

1. Create z-normal iso-surface `pp_plane_zc_<z>` at \(z = \tfrac{1}{2}(z_{\min}+z_{\max})\) from measured fluid bounds (equals \(z = 0\) on the campaign channel-centred mesh; never a candidate list of \(h/2\) vs \(0\)).
2. For **each spacer cell** (not only evaluation cells): create temporary
   x-range iso_clip `pp_mid_clip_cb_cell_{N}`, area + **surface-massavg** salt
   reports, convert to mol/m³, then **delete** clip and reports.
3. Aggregate `c_b_window_mol_m3` over **evaluation cells only**
   (area-weighted mean of per-cell \(c_b\)).

### Cell 8.4b — Legacy whole-domain center-plane average

- Report `pp_c_bulk_center` = areaavg of salt on the same mid-plane **without**
  x-clip (includes buffers). Inventory / diagnostic only — **not** used as
  canonical \(c_b\).
- Columns: `c_bulk_center_*`.

### Segmented membrane CP (still under the 8.4 block in the script)

Calls `segmented_membrane_cp_metrics(...)`:

1. For each **spacer** cell on **combined** membranes: x-clip
   `pp_mem_clip_comb_{N}`; area / areaavg / facetmax|min on UDM-6/7/9.
2. For **evaluation** cells only: area-backed facetmin hygiene + **0.1%/99.9%
   high-end quantiles** (`pp_mem_ab_*` nested clips, deleted after each probe);
   form \(c_{p,\min}/c_{p,\max}\), \(k_N\), δ, CP_canon / L1 / L2.
3. Window and all-active aggregates; optional per-wall
   (`pp_mem_clip_w_upper_{N}` / `w_lower_{N}`) **without** quantile spread,
   reusing combined \(k_N\).

Raises if canonical window CP cannot be formed (extract fails hard).

### Cell 8.5 — Convert whole-domain center bulk units

- Python conversion between mass fraction and mol/m³ for `c_bulk_center_*`.

### Cell 9 — Build summary tables (Python only)

- Derives LMH consistency, mass-balance errors, wall shear **rate** (= stress/μ),
  per-cell ΔP and salt rise, periodic pressure metrics.
- Assembles long-format `summary_rows`, plus mass/pressure/shear side tables.

### Cell 10 — Write CSVs / JSON and exit Fluent

- Writes the five report artifacts under `post/reports/`.
- `require_canonical_cp_summary_columns` gates the wide CSV.
- `finally`: stop transcript if any, `solver.exit()` / `meshing.exit()`, restore
  Python cwd. Cell-7 planes/reports die with the session (not explicitly
  deleted one-by-one).

---

## 2. Fluent objects created

### Persistent until session exit (Cell 7 + mid-plane parent)

| Name pattern | Type | Count (1+7+2) | Lifetime |
| --- | --- | ---: | --- |
| `pp_plane_spacer_in`, `pp_plane_spacer_out` | x-normal iso-surface | 2 | session |
| `pp_plane_unit_cell_boundary_{i}` | x-normal iso-surface | 11 | session |
| `pp_plane_zc_*` | z-normal iso-surface | 1 | session |
| `pp_m_in`, `pp_m_out`, `pp_area_mem`, … | report definitions | ~80 | session |
| `pp_lmh_mass_balance*` , `pp_pressure_drop*` | single-valued expression reports | 4 | session |
| `pp_c_bulk_center` | surface-areaavg report | 1 | session |

### Temporary (create → compute → delete)

| Name pattern | Type | When | Lifetime |
| --- | --- | --- | --- |
| `pp_mid_clip_cb_cell_{N}` | x-range iso_clip of mid-plane | per spacer cell in Cell 8.4 | deleted after cell |
| `pp_mid_area_*`, `pp_mid_salt_*` | surface reports | same | deleted after cell |
| `pp_mem_clip_comb_{N}` | x-range iso_clip of membranes | per spacer cell in CP path | deleted in `finally` of segment |
| `pp_mem_{area,cm,jw,cp,...}_{tag}` | surface reports on that clip | per segment | deleted with segment |
| `pp_mem_ab_{label}_{tag}` | nested field iso_clip | per area-frac / quantile probe | **deleted after each probe** |
| `pp_mem_ab_area_*` | area report on nested clip | same | deleted after each probe |
| `pp_mem_clip_w_{upper\|lower}_{N}` | per-wall x-clip | eval cells only, no quantiles | deleted with segment |

`pp_q_*` names appear in **diagnostic** scripts
(`diagnose_cp_quantile_spread.py`), not in production extract.

### Rough Fluent call cost (one run, 1+7+2)

Order-of-magnitude, not instrumented:

| Phase | Approximate `compute` / create ops |
| --- | --- |
| Cell 7 create + Cell 8 compute | ~80 definitions + ~80 computes |
| Cell 8.25 reduction | a few reduction calls |
| Cell 8.4 mid-plane (7 spacer cells) | ~7 × (clip + 2 reports + 2 computes + deletes) ≈ 50–70 |
| Segmented CP, no-spread spacer cells (~3) | ~3 × (~10 surface reports/computes) |
| Segmented CP, spread on 4 eval cells | ~4 × (base ~10 + facet checks + ~2 quantiles × ~10–20 probes) ≈ 150–250 |
| Per-wall no-spread (2 × 4) | ~8 × ~10 |
| **Total** | **roughly 400–600 Fluent surface/report operations** |

Dominant cost today is evaluation-cell quantile bisection when
`compute_cp_spread=True`. With the post_config default `compute_cp_spread=False`,
that block is skipped (`cp_canon_rescale_delta_status=not_evaluated`); \(k_N\)
still runs. Re-enable for final mesh-study reporting.

### Measured wall-clock cost

**38.7 min** for `REF_empty` at 628k cells (the smallest mesh). Breakdown:

| fraction | phase |
|----------|--------|
| 62% | `segmented_membrane_cp` |
| 16% | Cell 7 report creation |
| 7% | Cell 8.4 mid-plane \(c_b\) |
| 7% | Cell 8 compute |
| 3.5% | `read_case_data` |
| 3.3% | Fluent launch |

Sub-timers: `surface_report_def_create` ~680 s across 135 definitions versus
~180 s of compute, so roughly **80% of the cost is PyFluent settings-API
round trips**, not Fluent computation. Cell-count-dependent phases are under
10% of the total. Do not treat `compute_cp_spread` as the cost lever
(default off; turning it off changed no reported CP).

Run-to-run variance on the same case: segmented CP phase 1438.7 / 1091.0 /
842.9 s. Any optimisation must be measured with repeats.

Profile before optimising. The older "~33 min/case with 91% unattributed"
figure is superseded.

### Batch log naming pitfall

`batch_postprocess_all_cases.py` log filenames carry `geo_id` and `run_id` but
**not** `mesh_id`. Several meshes that share a `run_id` (e.g. exploration
variants of the same operating point) **overwrite each other's logs**. That
cost two wrong diagnoses in the grid-exploration session. Prefer embedding
`mesh_id` in the log name, or write under the run leaf path, before trusting
a post log that is not unique for `(geo, mesh, run)`.

---

## 3. Column provenance

Wide CSV = one row from `summary_rows_to_wide_record(summary_rows)`.
Column count ~164 depends on layout (per-boundary and per-cell keys).

### A. Read directly from a Fluent report (Cell 8 `computed_values`)

Renamed or copied into summary:

| Wide column(s) | Fluent report |
| --- | --- |
| `m_in`, `m_out` | `pp_m_in`, `pp_m_out` |
| `area_mem` | `pp_area_mem` |
| `pp_udm_area_sum` | UDM membrane-area volume report |
| `lmh_mass_balance`, `lmh_mass_balance_signed` | expression reports |
| `lmh_udm_{avg,max,min}` | `pp_lmh_udm_*` on membranes |
| `p_in_avg`, `p_out_avg`, `pressure_drop` | `pp_p_*` / expr |
| `p_spacer_in_avg`, `p_spacer_out_avg`, `pressure_drop_spacer` | spacer plane reports / expr |
| `jw_*`, `cm_*` / `cm_mol_m3_*`, `cp_inlet_*`, `salt_flux_*` | membrane UDM surface reports |
| `wall_shear_{avg,max,min}` | `pp_wall_shear_*` (`wall-shear` field) |
| `pp_p_unit_cell_boundary_{i}_avg` | per-boundary pressure |
| `pp_salt_mass_fraction_unit_cell_boundary_{i}_{avg,massavg}` | per-boundary salt |
| `pp_area_unit_cell_boundary_{i}` | per-boundary plane area |
| volume sink integrals | `pp_volint_*` |

### B. Computed in Python from other reports / Fluent results

| Column | Formula / rule (from code) |
| --- | --- |
| `boundary_permeate_mass_flow` | `abs(m_in + m_out)` |
| `lmh_difference_mass_balance_minus_udm` | `lmh_mass_balance - lmh_udm_avg` |
| `lmh_relative_difference` | that difference / `lmh_mass_balance` |
| `pressure_drop_per_m` | `pressure_drop / domain_length_m` |
| `pressure_drop_spacer_per_m` | `pressure_drop_spacer / spacer_length_m` |
| `wall_shear_rate_*` | `wall_shear_* / mu` (`cfg.mu`) |
| `water_sink_volume_integral_UDM1` | `volint(TOTAL_S) - volint(SI)` |
| `mass_balance_error_boundary_minus_total_sink` | `boundary_permeate - abs(total_sink)` |
| `mass_balance_relative_error` | error / `abs(total_sink)` |
| `pp_pressure_drop_cell_{N}` | \(p_{N-1} - p_N\) on unit-cell planes |
| `pp_salt_mass_fraction_rise_cell_{N}` | salt areaavg\(_{N}\) − areaavg\(_{N-1}\) |
| `pp_salt_molar_concentration_*_mol_m3` | Yi → mol/m³ with `rho`, `MW` |
| `c_bulk_center_mol_m3_avg` / mass-fraction twin | unit conversion of `pp_c_bulk_center` |
| `c_b_window_mol_m3` | area-weighted mean of mid-plane \(c_b\) over **eval cells** |
| `pp_c_b_midplane_cell_{N}_mol_m3` | per-cell mid-plane massavg → mol/m³ |
| **`cp_canon_window_avg` / `_max`** | see formulas below |
| `cp_L1_window_*`, `cp_L2_window_*` | window aggregates of Gu2017 / UDM-9 |
| `cp_*_all_active_*` | same definitions over all spacer cells with \(c_b\) |
| `pp_cp_canon_cell_{N}`, `pp_cp_L1_cell_{N}`, `pp_cp_L2_cell_{N}` | per eval cell |
| `pp_cp_canon_rescale_k_cell_{N}`, `…_delta_cell_{N}` | \(k_N\), δ |
| `cp_canon_rescale_delta_max` | max δ over eval cells |
| quantile / facetmin hygiene columns | `cm_q_hi_*`, `jw_q_lo_*`, `cm_min_*`, … |

**Physically important formulas**

- **`lmh_mass_balance`** (Fluent expression, then copied):  
  \(\mathrm{LMH} = \lvert \dot m_\mathrm{in}+\dot m_\mathrm{out}\rvert / (\rho\, A_\mathrm{mem}\,(1-f_\mathrm{blocked})) \times 3.6\times10^6\).

- **`lmh_udm_avg`**: areaavg of UDM LMH on active membranes (solver-stored).

- **`cp_inlet_avg`**: areaavg of UDM-9 on active membranes (L2 / inlet-\(c_0\) form). **Whole membrane**, not window-restricted.

- **`c_b_window_mol_m3`**: mid-plane mixing-cup salt, x-clipped per cell, then  
  \(\sum (c_{b,N} A_N) / \sum A_N\) over evaluation cells.

- **`c_bulk_center_mol_m3_avg`**: whole mid-plane areaavg (buffers included). **Not** the canonical denominator.

- **Per-cell canonical CP** (`pp_cp_canon_cell_N`):  
  \(k_N = (c_0 - c_{p,\mathrm{avg},N}) / (c_{b,N} - c_{p,\mathrm{avg},N})\),  
  \(M_{\mathrm{canon},N} = M_{\mathrm{UDM9},N}\, k_N\),  
  with \(c_{p,\mathrm{avg}} = B\,c_m/(J_w+B)\) from segment averages;  
  spread for δ uses area-backed \(c_{p,\min}\) and quantile \(c_{p,\max}\) (see `docs/metrics_conventions.md`).

- **`cp_canon_window_avg` / `_max`**: area-weighted mean / pointwise max of per-eval-cell canon (max uses facetmax UDM-9 × same \(k_N\)).

- **`cp_L1_window_avg`**: area-weighted \(c_m / c_b\) (Gu 2017).

- **`cp_L2_window_avg`**: area-weighted UDM-9 (same field as `cp_inlet` but **window cells only**).

- **`pressure_drop_spacer`**: Fluent `pp_p_spacer_in_avg - pp_p_spacer_out_avg` (active span edges).

- **`pp_pressure_drop_cell_N`**: adjacent unit-cell boundary pressures.

- **`wall_shear_avg`**: Fluent wall-shear stress areaavg; rates divided by `mu` in Python.

- **`mass_balance_relative_error`**:  
  \((\lvert\dot m_\mathrm{in}+\dot m_\mathrm{out}\rvert - \lvert\int s_\mathrm{total}\,dV\rvert) / \lvert\int s_\mathrm{total}\,dV\rvert\).

- **`pp_salt_mass_fraction_rise_cell_N`**: downstream − upstream areaavg Yi on unit-cell planes.

### C. Copied from mesh / run manifest / layout

| Column | Source |
| --- | --- |
| `geo_name`, `case_name` | post overrides / config |
| `domain_length_m`, `spacer_x_in_m`, `spacer_x_out_m`, `spacer_length_m` | layout from mesh/run manifest |
| `pp_unit_cell_boundary_{i}_x_m` | layout boundary abscissae |
| LMH blocked-area factor (inside expression) | `run_manifest["membrane_blocked_area_frac"]` |
| `c_inlet_ref` (used in \(k_N\), may appear as `c_inlet_ref_mol_m3`) | `cfg.c_inlet_ref` |

### D. Constant / configuration echo

| Column | Source |
| --- | --- |
| `mu` (in shear table / sometimes wide) | `cfg.mu` |
| `expected_outlet_gauge_pressure` | `cfg.outlet_gauge_pressure` (pressure CSV; may be absent from wide) |
| threshold / diagnostic strings | config + exception text |
| `c_b_window_salt_field`, `c_b_window_is_mass_fraction_field` | which field succeeded |
| `cp_scalar_rescale_guard_threshold` | `CP_SCALAR_RESCALE_GUARD_THRESHOLD` |

### Flux-massflow decomposition

A flux-massflow report on a zone adjacent to a UDF mass source returns three
keys, with values wrapped in lists:

| key | meaning |
|-----|---------|
| `'pp_m_in'` | bare; includes the source term |
| `'pp_m_in(without-sources)'` | physical boundary flux |
| `'pp_m_in(User Mass Source)'` | integrated source |

Identity, verified to full precision: bare = without-sources + mass_source.
The physical value is `(without-sources)`: the inlet value equals
\(\rho U_{\mathrm{TARGET}} A_{\mathrm{inlet}}\) to eight digits, and a
velocity-inlet flux is not free. Fluent's transcript echo and its expression
engine both use without-sources. `m_in` / `m_out` now carry without-sources.

The bare values look like a swap between inlet and outlet because the same
source term is added to both boundaries — an algebraic identity, not a
binding error. Only `pp_m_in` and `pp_m_out` have multi-key payloads out of
76 reports; surface reports are single-key.

### Load-bearing column guards

`LOAD_BEARING_REPORT_NAMES` (14 names, verified against the 76 actual Cell 7
reports) and `LOAD_BEARING_SUMMARY_METRICS` (23, including the pressure
group) live in `src/ro/fluent_report_helpers.py`. Extract exits non-zero if
any of them would be written empty. The guards run **after** the CSV and raw
JSON are written so the artifacts survive for diagnosis.

A Cell 8 `except Exception` was silently blanking the mass-closure chain
while the run reported success. That is why the guards exist.

---

## 4. What the evaluation window does

For `1+7+2` and `n_lead_excluded=3`, `n_trail_excluded=0`:

- Active cells: **2–8**
- Evaluation cells: **5–8**
- Inlet buffer cell **1** and first three active cells **2–4** are excluded from
  window aggregates; outlet buffers **9–10** are never active membrane cells.

### Safe to compare across geometries (window / eval-scoped)

Prefer these for spacer-performance claims:

- `cp_canon_window_avg`, `cp_canon_window_max`
- `cp_L1_window_avg`, `cp_L1_window_max`
- `cp_L2_window_avg`, `cp_L2_window_max`
- `c_b_window_mol_m3`
- `pp_cp_*_cell_{5..8}`, `pp_c_b_midplane_cell_{5..8}_*`
- `cp_canon_rescale_delta_max` (guard over eval cells)
- Periodic helpers derived from the window (e.g. `pp_pressure_drop_periodic_per_m`)

### Whole-domain or all-active (entrance-contaminated or broader)

Treat carefully when comparing geometries:

- `cp_inlet_avg` / max / min — **entire active membrane**, not window
- `jw_*`, `cm_*`, `lmh_udm_*`, `salt_flux_*`, `wall_shear_*`, `area_mem`
- `m_in`, `m_out`, full `pressure_drop`, volume mass-balance
- `c_bulk_center_*` — mid-plane **including buffers**
- `cp_*_all_active_*` — all spacer cells with \(c_b\), not lead-trimmed
- `pp_*_cell_N` for \(N\notin\{5,6,7,8\}\) — still written for active cells 2–4

### Hybrid

- `pressure_drop_spacer` / `_per_m` — full **active span** edges (cells 2–8),
  not the 5–8 window alone.
- `pp_pressure_drop_cell_N` / salt rise — available for every active cell;
  only 5–8 are inside the evaluation window.

---

## 5. Why Fluent is needed at all

### Requires a live Fluent session (not available as plain files in this pipeline)

These are built with surfaces, iso_clips, report definitions, or reductions on
the loaded case/data + UDM fields:

- All membrane UDM surface statistics (`jw`, `cm`, `lmh_udm`, `cp_inlet`, salt flux)
- Wall shear surface statistics
- Mid-plane \(c_b\) clips and massavg
- Segmented CP / quantiles / δ
- Unit-cell boundary plane pressure and salt reports
- Spacer / inlet / outlet surface pressure and flux reports
- Volume integrals of UDM sources
- Salt mass-fraction cell-count reductions

The solver’s `.cas.h5` / `.dat.h5` store fields, but this stage does not read
them with a file-only path; it always launches Fluent and re-queries.

### Could in principle come from solver monitors / existing files — UNKNOWN extent

The extract script never reads Fluent `.out` monitor files. Whether the
campaign solver already writes equivalent monitors for mass flow, ΔP, or
report-file dumps is **UNKNOWN** from this code path alone. Even if some
scalars existed in monitors, **window \(c_b\), segmented CP, and UDM membrane
areaavgs** are not produced as monitor time-series by this repository’s
extract logic.

### What relaunch costs that are not “new physics”

- Process start + `read_case_data`
- Possible Fluent-side UDF load/rebuild (not invoked explicitly in the script)
- Re-creating dozens of report definitions every run (no reuse across runs)

Cheaper extract would need either persisted report definitions, a file-based
reader for UDMs, or dropping quantile/mid-plane work — none of that exists
today.

---

## 6. What is NOT produced with contours and shear disabled

With `--run-reports --no-run-pyensight-contours --no-run-shear`:

### Report stage still writes

- All of §1–§3, including **`wall_shear_report.csv`** and wide columns
  `wall_shear_*` / `wall_shear_rate_*`.
- Those are **scalar surface reports** on membrane walls (`wall-shear` field),
  not images.

### `pyensight_contour_export.py` adds (when enabled)

- PNG contours under `post/figures/contours/` for configured fields
  (typically CP, water flux, LMH, salt flux; shear_rate optional in its
  `--fields` list).
- Status / bounds JSON beside the figures.
- Uses **PyEnSight**, not the report-stage surface-report path.
- Does **not** refresh `summary_metrics_wide.csv`.

### `pyfluent_shear_contour_export.py` adds (when enabled)

- Membrane **shear-rate contour images** (CFF or matplotlib fallback) and
  `shear_contour_status.json`.
- Separate Fluent (or fallback) graphics session aimed at presentation
  images.
- Does **not** replace or write the report-stage `wall_shear_report.csv`.

**Summary:** report-stage “shear” = numbers in CSV; shear/contour stages =
figures (+ status JSON). Disabling the image stages does not remove shear
scalars from the report extract.
