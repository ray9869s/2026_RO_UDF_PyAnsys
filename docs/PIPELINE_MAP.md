# PIPELINE_MAP

Accurate map of the live tree as of the date this file was written. Derived by
reading `scripts/`, `src/ro/`, `configs/`, and `docs/` — not by memory.
Where the code does not settle a question, this document says **UNKNOWN**.

Environment assumed throughout: `RO_DATA_ROOT` absolute; `PYFLUENT_PROJECT_ROOT`
optional (else `ro.paths.project_root()` walks for `pyproject.toml`). Workers
need `pip install -e .` so `import ro` works outside pytest.

---

## 1 — Entry points

Every `scripts/*.py` file. Human entry points first; then workers typically
spawned by a batch driver; then leftovers.

### 1.1 Human entry points (orchestrators / tools)

| Script | Purpose | Command form | Required env |
|---|---|---|---|
| `batch_meshing.py` | Sequential meshing batch from `configs/batch_config.py` | `python scripts/batch_meshing.py [--case-set exploratory\|production] [--geo-id ID] [--mesh-id ID] [--dry-run]` (default `--case-set exploratory` reads `mesh_batch_cases`; `production` reads `production_mesh_batch_cases`) | `RO_DATA_ROOT` |
| `batch_solver_sweep.py` | Sequential solver sweep from `batch_config.py` | `python scripts/batch_solver_sweep.py [--case-set exploratory\|production] [--geo-id ID] [--outlet-gauge-pressure PA] [--dry-run]` (default `exploratory` reads `solver_sweep_cases`; `production` reads `production_solver_sweep_cases`; pressure filter e.g. `6.0e6` keeps the p6M column, 93 cases) | `RO_DATA_ROOT` |
| `batch_solver_rerun.py` | Continue / strategy-rerun embeds Fluent; does **not** spawn `solver_code_260616.py` | `python scripts/batch_solver_rerun.py [--candidates-csv PATH] [--results-root PATH] [--output-dir PATH] [--logs-dir PATH] [--report-script PATH] [--family ID] [--geo-id ID] [--mesh-id ID] [--run-id ID] [--convergence-status …] [--limit N] [--start-index N] [--dry-run] [--continue-on-error] [--force] [--skip-existing-rerun-success] [--additional-iterations N] [--solver-strategy …] [many strategy / residual / launch flags — see `parse_args`]` | `RO_DATA_ROOT` (path defaults) |
| `batch_postprocess_all_cases.py` | Inventory-driven reports + contours + shear | `python scripts/batch_postprocess_all_cases.py [--inventory-csv PATH] [--results-root PATH] [--python-exe PATH] [--family] [--geo-id] [--mesh-id] [--run-id] [--case-status] [--fields] [--membrane-surface] [--run-reports] [--auto-run-missing-reports / --no-…] [--run-pyensight-contours / --no-…] [--run-shear / --no-…] [--skip-existing / --no-…] [--force] [--dry-run] [--limit] [--start-index] [--continue-on-error / --no-…] [view/shear/cff flags]` | `RO_DATA_ROOT` |
| `batch_report_extract.py` | Report-only batch from `configs/batch_post_config.py` | `python scripts/batch_report_extract.py [--case-set exploratory\|production] [--geo-id ID] [--outlet-gauge-pressure PA] [--dry-run] [--report-skip]` (no `--case-set`: `post_cases` or walk run manifests; `production` reads `production_solver_sweep_cases`; unknown filter values error) | `RO_DATA_ROOT` |
| `case_inventory.py` | Read-only scan of run leaves → inventory CSVs | `python scripts/case_inventory.py [--results-root PATH] [--output-dir PATH] [--family] [--geo-id] [--mesh-id] [--run-id] [--max-iter N] [--include-hidden] [--verbose] [--dry-run]` | `RO_DATA_ROOT` |
| `rebuild_mesh_ledger_from_logs.py` | Rebuild `mesh_ledger.csv` from `mesh_log_*.txt` | `python scripts/rebuild_mesh_ledger_from_logs.py [--results-root PATH] [--output PATH] [--dry-run]` | `RO_DATA_ROOT` |
| `residual_measurement_report.py` | Offline residual/QoI stationarity report (no Fluent) | `python scripts/residual_measurement_report.py [--results-root PATH] [--output-dir PATH] [--family] [--geo-id] [--mesh-id] [--run-id] [--window N] [--residual-target F] [--max-iter N] [--include-hidden] [--dry-run] [--verbose]` | `RO_DATA_ROOT` |
| `make_summary_figures.py` | Plot aggregate post summary CSVs | `python scripts/make_summary_figures.py [--summary-csv PATH] [--status-csv PATH] [--out-dir PATH]` | `RO_DATA_ROOT` (defaults) |
| `rebuild_mesh_manifest.py` | Rebuild an **existing** `manifest.json` from mesh log + `.msh.h5` (dry-run default; `--allow-field` required to write) | `python scripts/rebuild_mesh_manifest.py --geo-id ID \| --family F \| --all [--allow-field NAME] [--apply]` | `RO_DATA_ROOT` |
| `backfill_mesh_manifest_fields.py` | Add missing required keys to an **existing** mesh `manifest.json` from the geometry registry | `python scripts/backfill_mesh_manifest_fields.py --geo-id ID \| --family F [--apply]` | `RO_DATA_ROOT` |
| `backfill_run_manifest_fields.py` | Same for run manifests | `python scripts/backfill_run_manifest_fields.py --geo-id ID \| --family F [--apply]` | `RO_DATA_ROOT` |
| `check_d2450_fresh_start_reference.py` | Non-gating diagnostic vs archive constants for hardcoded `D2450_a45` / peel2 / `u0p2_p6M` | `python scripts/check_d2450_fresh_start_reference.py` (argparse; **no flags**) | `RO_DATA_ROOT` |
| `print_cp_definition_table.py` | Print CP definition comparison table from JSONs | `python scripts/print_cp_definition_table.py [--left-json PATH] [--right-json PATH] [--plug-json PATH] [--parabolic-json PATH] [--left-label S] [--right-label S] [--left-cells N] [--right-cells N]` | none |
| `pyfluent_field_check.py` | Diagnostic field / UDM sanity check | `python scripts/pyfluent_field_check.py [--config PATH] [--family] [--geo-id] [--mesh-id] [--run-id] [--geo-name] [--case-name] [--with-fluent] [--fail-on-warn]` | `PYFLUENT_POST_CONFIG` optional; `RO_DATA_ROOT` for four-id selection |
| `pyensight_extra_figures.py` | Extra EnSight figures (not spawned by `batch_postprocess_all_cases`) | `python scripts/pyensight_extra_figures.py [--family] [--geo-id] [--mesh-id] [--run-id] [--geo-name] [--case-name] [--results-dir PATH] [--run] [--dry-run] [--active-x-min] [--active-x-max]` | `RO_DATA_ROOT`; optional `PYFLUENT_EXTRA_FIGURES_OVERRIDES` |
| `probe_species_numerics_context.py` | Live Fluent species-numerics probe matrix (no save/iterate) | `python scripts/probe_species_numerics_context.py [--template-case PATH] [--mesh-file PATH] [--product-version] [--processor-count] [--graphics-driver] [--ui-mode] [--start-timeout] [--dry-run]` | `RO_DATA_ROOT` for default mesh path |

### 1.2 Workers (invoked by batch drivers or directly)

| Script | Purpose | Command form | Required env |
|---|---|---|---|
| `meshing_code_260616.py` | Fluent meshing worker | `python scripts/meshing_code_260616.py [--force]` | `RO_DATA_ROOT`; optional `PYFLUENT_RUN_CONFIG`, `PYFLUENT_RUN_OVERRIDES`, `PYFLUENT_SKIP_VALIDATION` (batch pops the last two of the trio aside from overrides) |
| `solver_code_260616.py` | Fluent solve worker | `python scripts/solver_code_260616.py` (no argparse) | same as meshing worker |
| `pyfluent_report_extract.py` | Per-run report extraction | env-driven (no argparse) | `PYFLUENT_POST_CONFIG` (default `configs/post_config.py`), `PYFLUENT_POST_OVERRIDES`; `RO_DATA_ROOT` via path helpers |
| `pyensight_contour_export.py` | EnSight contour PNGs | `python scripts/pyensight_contour_export.py [--config] [--family] [--geo-id] [--mesh-id] [--run-id] [--geo-name] [--case-name] [--fields] [--dry-run] [--skip-existing] [many range/view/legend flags]` | `PYFLUENT_POST_CONFIG`; `RO_DATA_ROOT` |
| `pyfluent_shear_contour_export.py` | Fluent shear contour export | `python scripts/pyfluent_shear_contour_export.py [--config] [--family] [--geo-id] [--mesh-id] [--run-id] [--geo-name] [--case-name] [--dry-run] [--skip-existing] [--cff-file] [--cff-name] […]` | `PYFLUENT_POST_CONFIG`; `RO_DATA_ROOT` |

### 1.3 Leftover / probe / not campaign entry points

Marked **not** a normal human campaign entry. All have `__main__` but are one-off Fluent schema dumps or throwaways.

| Script | Classification | Why |
|---|---|---|
| `_probe_add_boundary_layers.py` | leftover probe | Hardcoded main; dumps BL task schema; no argparse |
| `_probe_improve_task.py` | leftover probe | Hardcoded main; dumps Improve Surface Mesh args for fixed geo |
| `_probe_periodic_after_surface.py` | leftover probe | Hardcoded main; surface mesh then dumps periodic args |
| `_tmp_probe_reduction.py` | throwaway | Hardcoded diamond/`u0p2_p6M` reduction probe; stdout only |
| `_tmp_cell_profile.py` | throwaway | Hardcoded identity; writes `cell_profile_*.csv` under a run |
| `scripts/analysis/nacl_analyze.py` | leftover scraper | Positional transcript argv; no `ro` imports; no `__main__` guard pattern of campaign tools |
| `scripts/analysis/nacl_decay.py` | leftover scraper | same |
| `scripts/analysis/nacl_ptp.py` | leftover scraper | same |
| `scripts/analysis/period.py` | leftover scraper | same |

**Spawn map**

```
batch_meshing.py                 → meshing_code_260616.py
batch_solver_sweep.py            → solver_code_260616.py
batch_report_extract.py          → pyfluent_report_extract.py
batch_postprocess_all_cases.py   → pyfluent_report_extract.py
                                 → pyensight_contour_export.py
                                 → pyfluent_shear_contour_export.py
batch_solver_rerun.py            → embedded Fluent (not solver_code)
                                 → optional pyfluent_report_extract.py with
                                   --geo-name/--case-name (code detects those
                                   flags are absent on the report worker and
                                   defers; CONFLICT with a “run report after
                                   success” expectation)
```

---

## 2 — Stage-by-stage flow

There is **no automated geometry/CAD stage** in `scripts/`. Discovery `.dsco`
files are expected under `RO_DATA_ROOT/geometries/` (created outside this repo).

Ordered stages the code implements:

### Stage A — Geometry (external)

| | |
|---|---|
| Entry | **None in-repo.** Manual Ansys Discovery / archive copy. |
| Reads | UNKNOWN (outside this repo) |
| Writes | `RO_DATA_ROOT/geometries/{family}/{geo_id}/{geo_id}.dsco` |
| `ro` deps | none for creation; `geometry_dir` / `campaign_geo_ids` validate ids when used later |
| Prerequisite | none |

### Stage B — Mesh

| | |
|---|---|
| Entry | `python scripts/batch_meshing.py` **or** direct `meshing_code_260616.py [--force]` with overrides |
| Reads | `configs/batch_config.py` + `configs/run_config.py`; geometry `.dsco` |
| Writes | `meshes/{family}/{geo_id}/{mesh_id}/` → `{geo_id}_{mesh_id}.msh.h5`, `mesh_log_{mesh_id}.txt`, `manifest.json` (success only), `mesh_run_record.json` (`finally`, success or failure), optional `*_surface_checkpoint.msh.h5`; upserts `inventory/mesh_ledger.csv` (batch) |
| `ro` deps | `paths`, `manifest`, `campaign_geometry`, `mesh_common`; batch also `solver_common.merge_batch_case_overrides` |
| Prerequisite | Stage A leaf exists |

### Stage C — Solve

| | |
|---|---|
| Entry | `python scripts/batch_solver_sweep.py` **or** direct `solver_code_260616.py` |
| Reads | mesh `.msh.h5` (+ mesh `manifest.json`); `templates/`; `udfs/{udf_source_file_name}`; run/batch configs |
| Writes | `runs/{family}/{geo_id}/{mesh_id}/{run_id}/` → `{geo_id}_{run_id}_final.{cas,dat}.h5`, solver logs / `fluent-*.trn`, run `manifest.json`, case-local UDF copy; may lazy-fill mesh `inlet_profile_G` |
| `ro` deps | `paths`, `manifest`, `campaign_geometry`, `solver_common`, `domain_layout`, `lmh_metrics`, `fluent_report_helpers`, `udm_layout` |
| Prerequisite | Stage B hashed mesh (and mesh manifest for overwrite guards / layout) |

**Parallel path (not the sweep worker):** `batch_solver_rerun.py` loads existing finals and continues under strategy flags; writes under `inventory/solver_rerun*` and may rewrite cas/dat in the run leaf.

### Stage D — Inventory / residual diagnostics (offline)

| | |
|---|---|
| Entry | `case_inventory.py`; optionally `residual_measurement_report.py`, `rebuild_mesh_ledger_from_logs.py`, `check_d2450_fresh_start_reference.py` |
| Reads | run (or mesh) tree + manifests / transcripts |
| Writes | `inventory/case_inventory*.csv|json|txt`, `rerun_candidates.csv`, `postprocess_candidates.csv`; residual CSVs; rebuilds `mesh_ledger.csv` |
| `ro` deps | `manifest`, `paths`, `solver_common`; residual also `residual_transcript` |
| Prerequisite | Stage C leaves (inventory raises if `runs/` missing) |

### Stage E — Post (per-run reports / figures)

| | |
|---|---|
| Entry | `batch_postprocess_all_cases.py` and/or `batch_report_extract.py`; workers above |
| Reads | inventory compact CSV (postprocess) or `batch_post_config`; finals + mesh layout via mesh manifest |
| Writes | `{run}/post/reports/*`, `{run}/post/figures/**`; batch status under `inventory/batch_postprocess*`; optional aggregates `inventory/all_cases_post_summary.csv`, `all_cases_post_status.csv` |
| `ro` deps | `paths`, `manifest`, `domain_layout`, `lmh_metrics`, `fluent_report_helpers`, `udm_layout`, `shear_cff_mu_guard` (shear only) |
| Prerequisite | Stage C finals; layout from mesh `manifest.json` (`layout_from_mesh_manifest`) |

### Stage F — Aggregate figures

| | |
|---|---|
| Entry | `make_summary_figures.py` |
| Reads | `all_cases_post_summary.csv` (+ optional status CSV) |
| Writes | `inventory/post_summary_figures/` |
| `ro` deps | `paths` |
| Prerequisite | Stage E aggregates (typically from `batch_report_extract.py` with aggregate outputs enabled) |

---

## 3 — Module inventory (`src/ro/`)

Line counts from `wc -l` at write time.

| Module | Lines | Purpose | Entry points / configs that import it |
|---|---:|---|---|
| `__init__.py` | 3 | Package marker | `import ro` in configs/tests |
| `manifest_errors.py` | 5 | `ManifestError` base | **ro-internal** (`manifest`, `manifest_validation`) + tests |
| `shear_cff_mu_guard.py` | 83 | Observe-only CFF μ vs config guard | `pyfluent_shear_contour_export.py` |
| `lmh_metrics.py` | 87 | LMH area / expression helpers | `solver_code_260616.py`, `pyfluent_report_extract.py` |
| `campaign_geo_ids.py` | 113 | geo_id whitelist + family mapping | **ro-internal** (`paths`, `manifest`, `campaign_geometry`) + tests |
| `cp_metrics.py` | 197 | Pure CP modulus / averaging helpers | **ro-internal** (`fluent_report_helpers`) + tests |
| `paths.py` | 207 | `data_root` / `mesh_dir` / `run_dir` / selectors | Nearly all scripts + `configs/{run,post,batch_post}_config.py` |
| `udm_layout.py` | 225 | UDM indices + `RO_ANALYTIC_CWALL` parse | `solver_code_260616.py`, `pyfluent_report_extract.py`, `pyfluent_field_check.py`, `configs/post_config.py`; also `fluent_report_helpers`, `manifest` |
| `campaign_geometry.py` | 390 | Per-geo geometry tables for manifest merge | `meshing_code_260616.py`, `solver_code_260616.py`; `manifest_validation` |
| `domain_layout.py` | 532 | Asymmetric layout records from mesh manifest | `solver_code_260616.py`, `batch_postprocess_all_cases.py`, `batch_report_extract.py`, `_tmp_cell_profile.py`; `fluent_report_helpers` |
| `manifest_validation.py` | 596 | Geometry / zone / metre-scale validation | **ro-internal** (`manifest`) + tests |
| `manifest.py` | 812 | Read/write/validate mesh & run `manifest.json` | meshing/solver/inventory/post/rerun/diagnostic scripts; `domain_layout` |
| `mesh_common.py` | 821 | Mesh metrics parse, ledger CSV, `mesh_run_record` IO | `meshing_code_260616.py`, `batch_meshing.py`, `rebuild_mesh_ledger_from_logs.py`, `configs/run_config.py` |
| `solver_common.py` | 831 | Case naming, input mode, convergence helpers, path-to-Fluent strings | `solver_code_260616.py`, `batch_solver_sweep.py`, `batch_solver_rerun.py`, `batch_meshing.py`, `case_inventory.py`, `residual_measurement_report.py`, `probe_species_numerics_context.py`, `configs/batch_config.py`; also `manifest` |
| `residual_transcript.py` | 839 | Parse Fluent residual tables; window stats | `residual_measurement_report.py` |
| `fluent_report_helpers.py` | 1367 | Shared Fluent report / unit-cell diagnostics | `pyfluent_report_extract.py`, `solver_code_260616.py`, `_tmp_*` |

**No `src/ro/` module is imported by nothing.** Four are only reached via other `ro` modules from scripts: `campaign_geo_ids`, `cp_metrics`, `manifest_errors`, `manifest_validation`.

---

## 4 — Dead and duplicate code

### 4.1 Defined but never called (repo-wide, only the defining file)

| Symbol | Where | Why believed dead |
|---|---|---|
| `cp_l2_bae_approx_expression` | `cp_metrics.py` | No references outside its definition |
| `build_window_cp_metric_keys` | `cp_metrics.py` | same |
| `lmh_denominator_area_report_name` | `lmh_metrics.py` | same |
| `upgrade_mesh_manifest_in_place` | `manifest.py` | Migration helper; `scripts/migrate_manifest_schema_v2.py` and its tests are gone; live path uses `write_mesh_manifest` |
| `upgrade_run_manifest_in_place` | `manifest.py` | same |
| `transcript_max_iteration` | `residual_transcript.py` | same |
| `require_ro_analytic_cwall` | `udm_layout.py` | same; callers use `parse_ro_analytic_cwall_from_case` / related |

### 4.2 Deliberate stub / trap (called only from tests)

| Symbol | Notes |
|---|---|
| `resolve_layout` | Always raises `RuntimeError("layout comes from the mesh manifest")`. Production uses `layout_from_mesh_manifest`. Exists so leftover callers fail loudly. |

### 4.3 Scripts that are not campaign wiring

All `_probe_*`, `_tmp_*`, and `scripts/analysis/*` (section 1.3). Nothing in `batch_*` references them.

### 4.4 Two paths doing the same job class

| Pair | Relationship |
|---|---|
| `mesh_run_record.json` vs `manifest.json` | Both written by `meshing_code_260616.py` from the same `mesh_metrics` / `cfg` on success; record also written on failure in `finally`. Ledger (`batch_meshing`) prefers the record; schema validation / layout / solvers use the manifest. **Same source values, different consumers and lifetimes — not independent physics.** |
| `path_to_fluent_str` vs `path_to_fluent_str_resolved` | Documented deliberate dual helpers in `AGENTS.md` (abspath vs resolve) |
| `solver_common` residual/stop-reason parsing vs `residual_transcript` | Parallel stacks: live worker stop classification vs offline measurement report |
| `batch_solver_sweep` → `solver_code_260616` vs `batch_solver_rerun` embedded Fluent | Two solve entry styles; different configs and artifact contracts |
| CLI flag `--results-root` | Means **runs** root in `case_inventory` / `residual_measurement_report` / `batch_postprocess`; means **inventory** root in `batch_solver_rerun.resolve_path_defaults`. Same flag name, contradictory defaults — confusing, not dead |

### 4.5 Tests vs removed functionality

| Item | Notes |
|---|---|
| `scripts/migrate_manifest_schema_v2.py` / `tests/test_migrate_manifest_schema_v2.py` | Removed; leftover API is `upgrade_*_manifest_in_place` (unused) |
| `tests/test_domain_layout.py` calling `resolve_layout` | Tests the intentional raise, not live layout |
| Docs snippets in `RESTRUCTURE_PLAN.md` still show older run-manifest field lists omitting `needs_lead_recheck` / `u_mean_source_mesh_id` while live `MESH_*` / `RUN_*` required-field tuples include them — **doc/code contradiction**, not a test |

### 4.6 Confusing for a fresh reader

- Header comments in `batch_meshing.py` / `batch_solver_sweep.py` still say `My_CFD_Project/01_Scripts/...`.
- `configs/batch_config.py` holds two distinct matrices. Exploratory `mesh_batch_cases` is the isolated `D0817_a30` `max085_min006_cpg7_bl4_peel2` mesh and `solver_sweep_cases` is that leaf’s `u0p3_p6M` solve (fresh template + `replace_mesh`, no GTS override). Parked 22-case mesh list is `_MESH_BATCH_CASES_EXPLORATORY`; parked GTS list is `_SOLVER_SWEEP_CASES_GTS_PILOT`; parked seven-case list is `_SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS`. Production `production_mesh_batch_cases` is the 31 campaign geos (9 diamond + 3 ML + 9 pillar + 9 sin + `REF_empty`) and `production_solver_sweep_cases` is 31 × 9 = 279. Default batch entrypoints stay exploratory; `--case-set production` is required to launch the 279. `--geo-id` and `--outlet-gauge-pressure` restrict the selected case-set (unknown values error; they do not silently empty the sweep). `--dry-run` prints the selected four-id list and skip decisions without launching Fluent.
- Dual metadata filenames (`manifest.json` + `mesh_run_record.json`) without a single glossary in code.
- Orchestrators take `--family --geo-id --mesh-id --run-id`; workers still accept `--geo-name` / `--case-name` as **filename labels** equal to those ids (post) or via overrides JSON (solve/mesh).
- `needs_lead_recheck` is written and validated but **never read** by any post/solve script to change behaviour (see §6).
- `validate_spacer_wall_zones` exists in `manifest_validation.py` but is called only from tests, not on the solver path. Import-zone count (`N boundary face zones`) is the cheap pre-mesh check; see `docs/GEOMETRY_DESIGN.md`.

### 4.7 Manifest schema and migration

Adding a field to `_GEOMETRY_FIELDS` in `src/ro/manifest.py` appends it to
**both** `MESH_MANIFEST_REQUIRED_FIELDS` and `RUN_MANIFEST_REQUIRED_FIELDS`.
`read_mesh_manifest` / `read_run_manifest` then raise on every existing leaf
that lacks the key. Commit `e493975` added
`membrane_blocked_area_frac_geometric` this way and broke 34 mesh leaves and
14 run manifests. Recovery is `scripts/backfill_mesh_manifest_fields.py` and
`scripts/backfill_run_manifest_fields.py` (json.load the raw file, fill
missing required keys from the registry, then validate).

New **optional** fields (`wavelength_m`, `amplitude_m`) are deliberately kept
**out** of `_GEOMETRY_FIELDS` so existing manifests stay readable.

`rebuild_mesh_manifest.py` rewrites an existing `manifest.json` from the mesh
log + `.msh.h5` + registry overlay. `--allow-field` is a write guard: a
non-empty diff that touches any other key aborts. Numeric diffs use a
relative tolerance because `3.465 * 1e-3` and `3.465e-3` differ by one ULP.

Neither backfill nor rebuild can create a **missing** `manifest.json`. That
case requires re-meshing (`skip_existing_mesh` keys on the `.msh.h5`, so a
leaf with a mesh file and no manifest is skipped forever until the file is
removed).

---

## 5 — File layout under `RO_DATA_ROOT`

Four-ID scheme (from `ro.paths`):

| Id | Regex / role | Directory level |
|---|---|---|
| `family` | `diamond\|ml\|pillar\|sin\|empty` | under `geometries/`, `meshes/`, `runs/` |
| `geo_id` | campaign geo token; forbids `_brgN` / `_Nc` legacy suffixes | under family |
| `mesh_id` | `max###_min###_cpg#_bl#_peel#` | under geo |
| `run_id` | `u#p#_p#M` optional `_label` | under mesh (runs only) |

Tree the code expects:

```
RO_DATA_ROOT/
  geometries/{family}/{geo_id}/{geo_id}.dsco
      # created: Stage A (external). Read by meshing.

  meshes/{family}/{geo_id}/{mesh_id}/
      {geo_id}_{mesh_id}.msh.h5          # Stage B success
      mesh_log_{mesh_id}.txt             # Stage B
      manifest.json                      # Stage B success (schema v2 mesh)
      mesh_run_record.json               # Stage B finally (ledger-shaped)
      optional *_surface_checkpoint.msh.h5

  runs/{family}/{geo_id}/{mesh_id}/{run_id}/
      manifest.json                      # Stage C (schema v2 run)
      {geo_id}_{run_id}_final.cas.h5
      {geo_id}_{run_id}_final.dat.h5
      solver_log_*.txt / fluent-*.trn
      dated UDF copy
      post/reports/…                     # Stage E
      post/figures/…                     # Stage E

  inventory/
      mesh_ledger.csv                    # Stage B batch or rebuild_*
      case_inventory.csv / _compact.csv / .json / _summary.txt
      rerun_candidates.csv               # Stage D (manual promote → active_*)
      active_solver_rerun_candidates.csv # NOT written by repo code; human
      postprocess_candidates.csv
      residual_measurement*.csv|txt
      all_cases_post_summary.csv / all_cases_post_status.csv
      batch_postprocess/… , batch_postprocess_logs/
      solver_rerun/… , solver_rerun_logs/
      post_summary_figures/

  archive/                               # frozen legacy; no live worker reads it
      (e.g. 03_Results/, pre_schema_*)   # provenance only
```

Project tree (not under `RO_DATA_ROOT`): `configs/`, `scripts/`, `templates/`, `udfs/`, `src/ro/`.

---

## 6 — Known gaps

Things the code does not yet handle that the campaign needs. No fixes proposed.

1. **Per-family UDF channel geometry / `inlet_profile_G` / `u_mean_ms` label**  
   `260822_RO_UDF.c` hardcodes `INLET_Z_BOTTOM`, `CHANNEL_HEIGHT`, `INLET_AREA_EXPECTED_M2` for the current diamond channel. All 31 campaign geometries share that channel (\(h = 0.770\,\mathrm{mm}\), \(W = 3.465\,\mathrm{mm}\)). `inlet_profile_G` is a campaign constant (Jensen excess of \(6\eta(1-\eta)\)); measured `REF_empty` vs D2450_a45 agree to \(3.2\times10^{-6}\). The solver still lazy-fills and compares per mesh as an integrity check. Run-manifest `u_mean_ms = u_target / G` is a **mislabeled profile coefficient**, not physical bulk velocity — see `docs/metrics_conventions.md`. No code path parameterizes those `#define`s by family.

2. **`needs_lead_recheck` is metadata-only**  
   Set `True` only for pillar in `campaign_geometry`, required on run manifests, validated for pillar/diamond. **No script reads the flag to alter CP window, inventory, or post.** Pillar lead recheck is therefore undeclared work relative to the flag’s presence. All 9 pillar geo_ids carry the flag (`_f320` variants were dropped).

3. **No in-repo geometry generation**  
   Pipeline assumes `.dsco` already exists. Campaign matrix expansion that needs new CAD is outside automation.

4. **Surface-size grid independence not closed**  
   `AGENTS.md` / restructure notes: bl4-vs-bl6 LMH/CP figures are wall-normal, not an `m_max` study. The `m_max` exploration on D2450_a45 is recorded in `docs/MESH_LANDSCAPE.md`; neither the `bl` nor the `m_max` axis is converged.

5. **MFBO layer**  
   Mentioned in `AGENTS.md` as planned, not implemented.

6. **`batch_solver_rerun` ↔ report extract CLI mismatch**  
   Rerun optionally wants `pyfluent_report_extract.py --geo-name/--case-name`; report worker is env/`PYFLUENT_POST_*` driven. Auto report-after-rerun is deferred in code.

7. **ml / sin / empty `needs_lead_recheck` policy**  
   Validation only forces pillar=`True` and diamond=`False`. Behaviour for `ml` / `sin` / `empty` beyond “must be bool” is UNKNOWN relative to campaign intent. All 9 pillar geo_ids are `True`.

8. **Manual `active_solver_rerun_candidates.csv`**  
   Inventory writes `rerun_candidates.csv`; nothing promotes to `active_solver_rerun_candidates.csv`. Human step is undocumented in code (only runbook prose).

9. **Archive vs live identity mapping**  
   Fresh-start diagnostic hardcodes archive-derived scalars; no automated archive→four-id importer. Recovering other archive cases is manual.

10. **Solver sweep list is two named sets**  
    Exploratory `mesh_batch_cases` is the isolated `D0817_a30` cpg7 mesh; `solver_sweep_cases` is that leaf’s `u0p3_p6M`. Production is `production_mesh_batch_cases` (31) and `production_solver_sweep_cases` (279). `python scripts/batch_solver_sweep.py` without `--case-set production` cannot launch 279.
