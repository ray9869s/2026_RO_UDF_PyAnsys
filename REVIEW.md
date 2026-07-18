# My_CFD_Project code review and refactoring plan

Review date: 2026-07-18

Scope: all tracked files, with detailed review of `My_CFD_Project/01_Scripts/`,
`My_CFD_Project/02_UDFs/`, and `My_CFD_Project/01_Templates/`. This was a static
review only. Fluent and EnSight were not launched, and no Ansys-dependent
behavior was executed.

## Executive assessment

The pipeline has a sensible high-level shape for license-bound research
automation: case matrices are separated from one-case workers, subprocesses
isolate Fluent sessions, per-case outputs are namespaced under
`03_Results/<geo>/<case>/`, and post-processing has explicit status artifacts.
The current 24-case campaign is also clearly encoded in `batch_config.py`.

The main weakness is that the repository is a collection of executable scripts,
not a small library with thin entry points. Naming, config merging, path
resolution, launch settings, convergence semantics, and compatibility fallbacks
are repeatedly implemented in large scripts. This makes apparently local edits
capable of changing campaign behavior elsewhere.

The most important correctness risks found are:

1. `06_batch_postprocess_all_cases.py` does not parse mesh-qualified case names,
   so generated report configs for `u0p1_p4M__<mesh>` cases record a missing
   inlet velocity and silently default the expected outlet pressure to 6 MPa.
2. `00_case_inventory.py` defaults to a 2,000-iteration target while the active
   campaign is capped at 1,000, and it can infer `CONVERGED` merely from a final
   case/data pair plus a summary CSV.
3. Requested contour fields can be recorded as `WARN` and return exit code zero;
   the batch orchestrator treats the subprocess as successful without promoting
   the worker's status JSON to the stage result.
4. JSON config overrides are applied as unrestricted, untyped attributes, while
   validation is incomplete and is skipped entirely for an explicit
   `PYFLUENT_RUN_CONFIG`.
5. The current-matrix filter in `07_batch_solver_rerun.py` accepts only plain
   names such as `u0p3_p4M`, excluding the 18 mesh-qualified sinusoidal cases in
   the active campaign.

The active campaign should not be refactored in place. The first implementation
work should be pure-Python characterization tests and low-risk helper extraction.
Core solver/rerun consolidation should wait until the campaign is complete.

## Architecture and data flow

### Meshing and solving

- `batch_config.py` owns the current matrices and common overrides.
- `batch_meshing.py` and `batch_solver_sweep.py` load executable Python config
  modules, merge dictionaries, serialize overrides to
  `PYFLUENT_RUN_OVERRIDES`, and launch one worker subprocess per case.
- `meshing_code_260616.py` and `solver_code_260616.py` dynamically load
  `run_config.py`, mutate the loaded module with the JSON payload, bind its
  attributes to globals, then execute the Fluent workflow.
- `solver_code_260616.py` copies the UDF into each case folder and patches only
  `SALT_YI_INDEX`; the membrane model constants remain literals in the C source.
- `07_batch_solver_rerun.py` is a separate continuation/recovery application. It
  reads inventory CSVs, directly launches Fluent, changes solver strategy,
  assesses convergence, stages outputs, optionally backs up/promotes final
  files, and writes its own plan/result artifacts.

### Post-processing

- `01_batch_report_extract.py` is the older matrix/config-driven report batch
  runner. It launches `01_pyfluent_report_extract.py` with a base config path and
  `PYFLUENT_POST_OVERRIDES`.
- `00_case_inventory.py` scans case files, reports, contour artifacts, and logs,
  then classifies cases and writes inventory CSV/JSON/text files.
- `06_batch_postprocess_all_cases.py` is the newer inventory-driven
  orchestrator. It can run reports, PyEnSight contours, and PyFluent shear
  contours for each selected case.
- `03_pyensight_contour_export.py`,
  `03b_pyfluent_shear_contour_export.py`, and
  `08_pyensight_extra_figures.py` are large one-case exporters with independent
  config/path/compatibility machinery.
- `05_make_summary_figures.py` consumes the optional cross-case CSVs produced by
  `01_batch_report_extract.py`, not the newer inventory/orchestrator result
  schema.

### Boundaries that should remain stable

- Existing entry-point filenames and environment variables.
- The `03_Results/<geo>/<case>/` layout and all current output filenames/schemas.
- Existing plain and mesh-qualified case names.
- UDF formulas and the Scheme CFF's computed value.
- Windows GUI/dx11 Fluent launch behavior.

## Findings

Severity meanings: **Critical** can corrupt or misidentify campaign data;
**High** can produce incorrect automation decisions or hide failed work;
**Medium** materially harms reliability or maintainability; **Low** is cleanup
or future-facing debt.

### Critical / high correctness risks

#### F-01 — Mesh-qualified report configs get incorrect operating metadata

Severity: **High**

`06_batch_postprocess_all_cases.py:505-511` uses
`re.fullmatch(r"u(\d+)p(\d+)_p(\d+)M", case_name)`. It therefore rejects every
name with a `__<mesh>` suffix. `write_report_config()` then writes
`inlet_velocity_value = None` and defaults
`outlet_gauge_pressure = 6.0e6` (`:522-535`). The report worker writes that
value as `expected_outlet_gauge_pressure`
(`01_pyfluent_report_extract.py:1138-1144,1241-1245`).

All 18 mesh-qualified sinusoidal cases therefore lose their parsed inlet
velocity in the generated config. The report worker does not currently emit an
inlet-velocity summary metric, so the observed CSV error is narrower: the 12
cases at 4 and 8 MPa carry incorrect expected-pressure metadata, while the six
6 MPa cases are correct only by accident. The measured Fluent pressure reports
are not replaced by this value, but downstream QA/provenance is wrong.

#### F-02 — Inventory can call an unverified solve `CONVERGED`

Severity: **High**

`00_case_inventory.py:845-846` assigns `CONVERGED` when a case/data pair and
`summary_metrics_wide.csv` exist, even with no positive solver convergence
evidence. Report extraction proves that a result file was readable; it does not
prove residual convergence.

The same script defaults `--max-iter` to 2,000 (`:363-393`) while the active
campaign is explicitly capped at 1,000 (`batch_config.py:76-90`). If a log has
iteration rows but no recognized "maximum iterations" phrase, reaching 1,000
does not satisfy the inventory's 2,000 target. The file/report fallback can then
upgrade the case to `CONVERGED`.

`run_config.py:145` also retains the normal-run default of 2,000 iterations.
The active batch correctly overrides it to 1,000; the issue is that inventory
classification has no durable record of that resolved per-case override.

This is unsafe for future MFBO: nonconverged preliminary points can be admitted
as trustworthy observations.

#### F-02b — `classify_case` can mark `likely_complete` while convergence is unresolved

Severity: **Medium**

`00_case_inventory.py:1423-1427` sets `likely_complete` when a case/data pair
and `summary_metrics_wide.csv` exist, even if `convergence_status` is
`POSSIBLY_INCOMPLETE` or `MAX_ITER_REACHED`. A case can therefore show
`convergence_status = MAX_ITER_REACHED` (or `POSSIBLY_INCOMPLETE`) while still
carrying `likely_complete = True` and, when contours are present,
`case_status = POSTPROCESSED_BASIC`.

This is separate from the F-02 `parse_logs()` fixes (default `--max-iter` and
artifact-only `CONVERGED` fallback). Tightening `likely_complete` should be its
own follow-up commit.

#### F-03 — Post-processing subprocess success does not mean requested outputs succeeded

Severity: **High**

In `03_pyensight_contour_export.py`, missing requested variables or surfaces are
often recorded as `WARN` (`:3613-3648,3985-3991`), while the process exits
nonzero only when a record is `FAILED` (`:4861-4865`). In
`03b_pyfluent_shear_contour_export.py`, partial side success similarly produces
`WARN` and exit zero (`:3854-3867,4073`). `08_pyensight_extra_figures.py`
deliberately skips many failed figures and returns zero whenever at least one
file was exported (`:2447-2459`).

`06_batch_postprocess_all_cases.py:417-478` maps return code zero directly to
stage `SUCCESS`. It detects output files later (`:807-829`) but does not use the
worker status JSON or requested-output completeness to revise that status.
Systematic missing fields can therefore look like a successful batch stage.
The orchestrator also returns zero when its inventory CSV is missing
(`:1027-1029`), which can make an automated invocation look successful despite
processing no cases.

Standalone and orchestrated contour expectations also differ:
`03_pyensight_contour_export.py:87` defaults to `cp_inlet`, `lmh`,
`wall_shear_rate`, and `velocity_midplane`, while `06` and inventory define the
basic PyEnSight set as `cp_inlet`, `water_flux`, `lmh`, and `salt_flux`.
Running `03` with no `--fields` can therefore complete successfully without
satisfying inventory's basic-contour definition.

Not every broad exception is wrong: many are compatibility probes across Ansys
API variants, and the exporters often aggregate diagnostics correctly. The
problem is the final success contract, not the existence of fallbacks.

#### F-04 — Override application is unrestricted and validation is inconsistent

Severity: **High**

Both active workers apply every JSON key with `setattr(cfg, key, value)`:

- `meshing_code_260616.py:35-47`
- `solver_code_260616.py:36-48`
- `01_pyfluent_report_extract.py:57-69`

Unknown keys are accepted, existing functions or imported names can be
overwritten, and values are not coerced against a schema. `run_config.py`
validates only a subset of fields. Examples:

- no `m_min <= m_max` relationship;
- inlet velocity need only be float-convertible, not positive;
- outlet/operating pressure and salt mass fraction are not range-validated;
- booleans enter numeric `isinstance(..., (int, float))` checks (`True` passes
  a positive check, while `False` passes a nonnegative check).

Additionally, `meshing_code_260616.py:49-50` and
`solver_code_260616.py:50-51` skip `validate_for_*()` whenever
`PYFLUENT_RUN_CONFIG` is set. A custom path therefore disables even the current
validation. The normal batch drivers remove that environment variable before
launch (`batch_meshing.py:83-84`, `batch_solver_sweep.py:215-216`), so this
specific bypass affects direct/custom-config invocation rather than the active
batch.

**Partial fix (2026-07-18):** `_require_positive_number` and
`_require_nonnegative_number` now reject `bool` values explicitly. Safe for the
active campaign: the only override bool (`run_calculation_enabled`) never
reaches these helpers; numerically validated override fields are int/float.

#### F-04 remainder (deferred — revisit after campaign completes)

Severity: **High** (unchanged overall finding)

Still open after the bool-guard partial fix:

- unrestricted `setattr` override application (unknown keys, function overwrite);
- no `m_min <= m_max` cross-field check;
- inlet velocity float-convertible only, not positive;
- outlet/operating pressure and salt mass fraction not range-validated;
- `PYFLUENT_RUN_CONFIG` custom-path invocation skips `validate_for_*()` entirely.

Address via centralized config merge with allowlists and stronger validation once
the active 24-case campaign is complete.

#### F-05 — Rerun selection excludes mesh-qualified active-campaign cases

Severity: **High**

`07_batch_solver_rerun.py:27` defines
`MATRIX_CASE_RE = r"^u\d+p\d+_p\d+M$"`, and `:844-865` drops nonmatches.
This excludes names such as
`u0p1_p4M__mesh_max100_min006_cpg3_bl3`, which represent 18 of the 24 cases in
the current matrix. Any inventory-driven recovery plan using this entry point
will silently count them as `non_matrix_case_name` rather than select them.

#### F-06 — Completion and convergence have different meanings but share “success” labels

Severity: **High**

`solver_code_260616.py:1627-1696` writes final case/data after `iterate()`
returns, whether Fluent met the residual target or exhausted the iteration
budget. `batch_solver_sweep.py:220-225` labels return code zero `SUCCESS`.
This is documented and intentional for the deadline campaign, but no durable
per-case run manifest records “completed but not converged” using the resolved
campaign settings. Downstream classification is left to heuristic log parsing,
which is affected by F-02.

Artifact verification has a separate failure hole:
`solver_code_260616.py:468-473` only prints a warning when an expected file is
missing. Its checks after the final write (`:1692-1696`) therefore allow the
worker to exit zero with a missing final `.cas.h5` or `.dat.h5`, which the
batch again labels `SUCCESS`.

For MFBO, solver execution success, numerical convergence, acceptance for
analysis, and post-processing completeness must be separate states.

#### F-20 — Universal `cp_inlet` WARN: EnSight bulk-center-average path fails on every post-processed case

Severity: **High** (systematic incorrect CP definition on all contour exports;
masked as `WARN` + exit 0 under pre-fix orchestration)

**Symptom (production):** All 46 post-processed cases with
`contour_export_status.json` show `summary.warn = 1` and `summary.failed = 0`.
The single `WARN` record is always `field_key = cp_inlet`. Typical message:

```text
WARN: bulk center avg failed (all_candidates_failed: bbox:
 failed_to_get_bounding_box | z=0: clip_failed(clip_cmd=ok,z=0...) | ...)
```

**Affected scope:** Every geometry and every case in the post-processed set (46/46
with status JSON). Not case-specific geometry; indicates a systematic EnSight API /
part-detection failure rather than sporadic mesh issues.

**Code path (`03_pyensight_contour_export.py`):**

1. **Strategy 0 — PyFluent CSV** (`:3664-3699`, `_read_pyfluent_bulk_center_avg`
   `:3342-3382`): reads `c_bulk_center_area_avg` from
   `post/reports/summary_metrics_wide.csv`. On production runs this either was
   unavailable at contour-export time or failed the plausibility gate (`:3672-3695`),
   because Strategy 1 was reached on every case.

2. **Strategy 1 — EnSight center-plane average** (`:3701-3706` →
   `compute_bulk_center_average` `:2875-3038`):
   - `find_fluid_volume_parts()` (`:2688-2700`) collects 3-D volume parts.
   - `get_part_bounding_box()` (`:2717-2724`) calls `_accumulate_parts_extents`
     (`:2703-2714`), which probes each part via `_first_success_for_object` →
     `attempt_object_bounds` (`:1081-1127`, `BOUNDS_API_ATTEMPTS` `:227-244`).
     Requires `zmin < zmax` (`:2722-2723`). Production diag
     `bbox: failed_to_get_bounding_box` means **no fluid part returned usable
     3-D extents** on this PyEnSight build.
   - Fallback z candidates `CENTER_PLANE_Z_CANDIDATES = [0.0, 0.000385]` (`:192`)
     are tried when bbox fails (`:2954-2956`).
   - For each z, `_try_create_clip_at_z` (`:2784-2830`) issues
     `session.ensight.clip.*` on selected volume part numbers. Production shows
     `clip_cmd=ok` followed by `clip_failed(...)` — the command language call
     succeeds but **no new part is detected** (`:2812-2826`,
     `no_new_parts_after_clip`), so the center plane is never created.
   - When every z candidate fails, `final_result = all_candidates_failed`
     (`:3036-3038`) and the cp_inlet handler records
     `WARN: bulk center avg failed ({plane_diag})` (`:3737-3744`).

3. **Fallback 1 — inlet reference** (`:3746-3777`): when `bulk_avg is None`,
   `create_cp_wall_direct(session, salt_desc, inlet_ref)` builds
   `CP_WALL_DIRECT = salt / inlet_ref` using `UDF_C_INLET_REF` or inlet mass
   fraction (`:3748-3758`). Adds a second WARN:
   `CP using inlet reference (...), center-plane avg unavailable`. **The PNG is
   still written** (`:4316-4324`): any warnings downgrade the record to
   `STATUS_WARN`, not `FAILED`; `export.image()` runs with the inlet-reference
   CP variable (or UDM_9 if that also fails — not observed in the 46-case
   pattern).

**What the export still produces:** A `cp_inlet` membrane PNG in the fixed
`[1.00, 1.15]` palette range, but CP is computed as **wall salt concentration
divided by the inlet reference**, not by the channel-center bulk average. Colorbar
metadata notes `bulk_reference_mode=inlet_reference_fallback` when the fallback
succeeds (`:4346-4350`). Physically this is a mislabeled concentration ratio, not
true CP; the WARN is diagnostically correct but pre-fix orchestration recorded
stage `SUCCESS`.

**Root-cause hypothesis:**

- **Primary:** PyEnSight 0.11.x on the server does not expose part bounds through
  any of the `BOUNDS_API_ATTEMPTS` attributes/methods on 3-D fluid volume parts,
  and/or returns degenerate z-extents (`zmin >= zmax`), so `get_part_bounding_box`
  always returns `None`.
- **Secondary:** The z-normal clip workflow assumes clip creates a **new** part
  discoverable by `PARTNUMBER` delta (`:2812-2817`). On this build the clip may
  modify geometry in place or register parts differently, so `plane_part` stays
  `None` even when `clip_cmd=ok`.
- **Universality** across geometries/cases rules out mesh-specific coordinates;
  points to API/session contract drift, not RO geometry.
- **Relation to deferred F-08 / #7 (case-only `load_data` fallback):** Possibly
  related if attempt-2 case-only loading leaves volume parts without full result
  geometry metadata, but **weak as sole explanation**: the other three contour
  fields (`water_flux`, `lmh`, `salt_flux`) export `SUCCESS` on the same
  session, so case/data loading is functional. Bounds and clip are more likely
  broken independent of load path; still worth verifying whether attempt-1
  dual-file load vs attempt-2 case-only changes part EXTENTS/clip behavior on
  the server (add post-load diagnostic logging in a future commit).

**Proposed fix (post-campaign; implement alongside deferred #7 review):**

1. **Prefer Strategy 0:** Ensure report extraction runs before contours in batch
   order (already typical) and widen/relax plausibility or log why CSV values
   are rejected; treat a valid `c_bulk_center_area_avg` as authoritative and
   skip EnSight AMEAN entirely when present.
2. **Repair Strategy 1 for current PyEnSight:**
   - Add server-side bounds diagnostic dump (reuse `attempt_object_bounds` on
     fluid volume parts) to identify which API works on 0.11.6.
   - Replace clip-by-PARTNUMBER-delta with a named-part lookup or
     `ensight.utils` clip helper if available on this build.
   - Consider reusing membrane-view bounds path (`get_membrane_view_bounds`,
     `:2727-2733`) which does not require `zmin < zmax`, only for z-mid
     estimation — not for CP itself.
3. **Tighten success contract (deferred F-03 #1):** Treat inlet-reference CP as
   `WARN` at orchestrator level (now recorded post commit `4d4d85a`) and
   eventually fail or flag campaigns that never achieve
   `bulk_reference_mode=center_plane_area_weighted_average*`.
4. **Do not change UDF physics or contour filenames**; fix is confined to bulk
   reference acquisition in `03`.

**Verification after fix:** On server, re-export one case per geometry; expect
`cp_inlet` record `SUCCESS` (or `WARN` only for genuine physics edge cases),
`bulk_reference_mode` containing `center_plane` or `pyfluent_report_csv`, and
`summary.warn = 0` for the basic four-field contour set.

### Maintainability and reliability

#### F-07 — The suspected `07` duplication is real in infrastructure, but not a safe wholesale extraction

Severity: **Medium**

`07_batch_solver_rerun.py` is roughly 6,000 lines and duplicates infrastructure
found elsewhere: Fluent launch kwargs/fallbacks, Fluent path conversion, case
folder/final-pair construction, subprocess reporting, CSV/JSON writing,
residual parsing, and convergence terminology. It does **not** simply duplicate
the normal solver workflow: it loads existing final case/data without
reinitialization (`:4979-5069`) and implements several recovery strategies,
readbacks, staged writes, backups, and promotion gates that do not belong in
`solver_code_260616.py`.

The safe refactor is to extract small, pure infrastructure seams and keep the
normal solve and continuation workflows separate. Replacing `07` with calls
into the normal solver worker would be a risky rewrite.

`execute_tui_best_effort()` (`:1771-1783`) treats any non-throwing command as
success without readback. `apply_pseudo_transient_ramp()` (`:2292-2309`) keeps
an `ATTEMPTED` status and can proceed even when both settings and all TUI
variants failed. This fallback needs an explicit required/optional contract and
verification, not blanket removal.

#### F-08 — Post-processing duplication is narrower and broader than suspected

Severity: **Medium**

Verified duplication:

- Python-config loading, `cfg_get`, path coercion, case paths, and Windows-path
  guards recur in `02_pyfluent_field_check.py`,
  `03_pyensight_contour_export.py`, and
  `03b_pyfluent_shear_contour_export.py`.
- `_has_windows_drive` / `_safe_mkdir` recur in `03`, `03b`, and `08`.
- The three-attempt PyEnSight `load_data` sequence is duplicated in `03`
  (`:2583-2645`) and `08` (`:1792-1828`).
- Case-name construction is duplicated between `batch_solver_sweep.py` and
  `01_batch_report_extract.py`.
- `01_batch_report_extract.py` and `06_batch_postprocess_all_cases.py` both
  select cases, derive paths, skip existing work, launch report workers, and
  summarize statuses.

Corrections to the original suspicion:

- `03b` uses PyFluent `read_case_data`, not the PyEnSight `load_data` fallback.
- `03`, `03b`, and `08` do not duplicate the operating-condition case-name
  regex. That parser is in `06`; naming construction is in the two batch
  drivers.
- `08` does not load `00_post_config.py`; it has a separate in-file `CONFIG`
  dictionary and `PYFLUENT_EXTRA_FIGURES_OVERRIDES`.

These differences matter: one oversized `_post_common.py` should not force all
three exporters into an artificial config model.

The duplicated PyEnSight fallback also has a correctness contract to tighten:
after a dual-file load fails, attempt two loads only the case filename and
accepts any non-throwing call as success. It may rely on EnSight discovering
the paired result file, but neither script verifies that result variables were
actually loaded before declaring the load successful. Shared extraction should
add post-load variable/data validation without removing compatible API
fallbacks.

#### F-09 — Config plumbing has more than four variants

Severity: **Medium**

The repository currently uses:

1. executable Python config paths (`PYFLUENT_RUN_CONFIG`,
   `PYFLUENT_POST_CONFIG`);
2. JSON env payloads (`PYFLUENT_RUN_OVERRIDES`,
   `PYFLUENT_POST_OVERRIDES`);
3. a generated per-case Python file from
   `06_batch_postprocess_all_cases.write_report_config()`;
4. per-script CLI arguments;
5. `08`'s in-file dict plus `PYFLUENT_EXTRA_FIGURES_OVERRIDES`;
6. `07`'s independent CLI-only config surface.

Precedence and allowed keys differ. `08` at least has an allowlist
(`:265-289,393-409`); the main run/report JSON mechanisms do not.

The generated report config also re-defaults physical values and timeouts
(`06:539-555`). With the normal base config present it copies the configured
300-second values, but its fallback literals are 600 seconds, while
`00_post_config.py:41-42` specifies 300. `01_pyfluent_report_extract.py:504-505`
and `03b_pyfluent_shear_contour_export.py:3320-3321` also default to 600 when
attributes are absent.

#### F-10 — Physical values currently agree, but there is no enforceable source of truth

Severity: **Medium**

The specifically suspected values do not currently drift:

- viscosity: `8.93e-4` in Python equals `0.000893` in the Scheme CFF;
- density: `998.2`/`998.20` agrees between Python and C;
- inlet reference concentration: `597.8268309` agrees between Python and C.

The drift surface is larger than those three values.
`03_pyensight_contour_export.py:169-185` duplicates the UDF's `A_perm`,
`B_perm`, `kappa`, `p_perm`, `MW_SALT`, `RHO_REF`, `MS_TO_LMH`, and
`C_INLET_REF` to reconstruct CP/LMH. `run_config.py:123-138` and
`00_post_config.py:17-19` duplicate material/post-processing values, and the
Scheme file hard-codes viscosity.

`03b_pyfluent_shear_contour_export.py:790-799` prefers loading the Scheme CFF
and returns success without proving its embedded divisor matches the configured
`mu`; the config-derived expression is used only by later fallbacks. A future
viscosity edit could therefore make native shear contours disagree with report
and field-data calculations. Likewise, `00_post_config.py` does not declare
`operating_pressure`, so `03` falls back to 101325 Pa independently of the
solver config.

The solver reads and validates `salt_density`, `salt_viscosity`,
`salt_molecular_weight`, `mixture_density`, `mixture_viscosity`, and
`mass_diffusivity`, but `solver_code_260616.py:1201-1234` only prints the
template material state rather than applying those configured values.
`mixture_density` is subsequently used in report-definition construction.
The template is therefore authoritative for actual material properties despite
the config names suggesting otherwise; this should be documented and checked,
not silently changed.

Because C and Scheme cannot import Python at runtime, “single source” should
mean a canonical Python constants module plus a deterministic check/generator.
The checked-in C and Scheme literals should remain reviewable, and parity tests
should fail if they diverge. No UDF equation needs to change.

#### F-11 — Windows-drive guards are duplicated and incomplete on POSIX

Severity: **Medium**

Several guards inspect `Path.parts` for a component exactly matching `C:`.
That catches `C:/...` parsed on POSIX, but a raw `C:\...` string is typically
one POSIX path component and can evade the check. Some scripts then call
`.resolve()`, turning it into a relative path under the current directory.

The report worker creates `post/` and `reports/` before validating input files
(`01_pyfluent_report_extract.py:87-128`) and has no equivalent guard. The
existing refusal behavior should be preserved and centralized, with tests for
forward-slash and backslash Windows paths on non-Windows hosts.

#### F-12 — Skip-existing checks are presence checks, not integrity/completion checks

Severity: **Medium**

Meshing skips on one mesh filename (`batch_meshing.py:67-76`); solving skips on
the final case/data pair (`batch_solver_sweep.py:208-212`). There is no atomic
completion marker, file-size/readability check, or resolved-config hash.
Interrupted or stale files can therefore suppress a rerun. The older report
batch is stronger: it validates required columns before skipping
(`01_batch_report_extract.py:278-293`).

The current `mesh_batch_cases` generate only the two sinusoidal
`mesh_max085_min005_cpg5_bl4` meshes (`batch_config.py:57-68`), while the
24-case solver campaign references existing `bl3` meshes, including coarse
sinusoidal meshes (`:147-177`). This may be intentional reuse of server
artifacts, and the solver preflight will fail safely if they are absent, but
the tracked configuration alone cannot regenerate all campaign prerequisites.

The newer orchestrator regresses that protection:
`06_batch_postprocess_all_cases.py:685-687` skips report extraction on
`summary_metrics_wide.csv` presence alone. It does not validate critical
columns or F-01's expected-pressure metadata, so a bad existing report can
persist silently under the default skip policy.

Changing active skip behavior is risky. First add manifests/validation as
opt-in diagnostics, then consider making them authoritative after the campaign.

#### F-13 — Naming is adequate for the fixed grid but unsafe for future MFBO

Severity: **Medium**

`batch_solver_sweep.py:32-50` rounds velocity to one decimal digit and pressure
to integer MPa. Different continuous design points can collide, and Python's
rounding at half steps is not an appropriate experiment identifier.
`01_batch_report_extract.py:82-91` repeats the same policy.

Do not change current names. Future MFBO should use an immutable unique
evaluation ID and store exact numeric parameters in a manifest, while retaining
the current human-readable case name as a compatibility label.

#### F-14 — Monolithic scripts and dynamic modules limit testability

Severity: **Medium**

Approximate hotspots are `07` (~6,000 lines), `03` (~4,900), `03b` (~4,100),
`08` (~2,500), and `00_case_inventory.py` (~1,900). Helpers are embedded beside
live Ansys code. Some workers guard execution with `if __name__ == "__main__"`,
but still import PyFluent, NumPy, matplotlib, or pandas at module import time.

Pure logic should move into small standard-library modules. Tests should import
those modules directly and never import or mock an Ansys session unless testing
an adapter boundary.

#### F-15 — Batch subprocesses have no wall-clock timeout or structured run manifest

Severity: **Medium**

Fluent startup/health timeouts do not bound an entire worker subprocess.
`subprocess.run()` in the meshing, solver, and report batch drivers can wait
indefinitely. Resolved config is primarily printed to consoles/transcripts,
with no common manifest containing config, source revision, Ansys versions,
input paths, and completion/convergence states.

This is an operational and reproducibility risk, but adding kill timeouts during
the active campaign would change behavior. Manifest-only recording is safer.

### Repository hygiene and lower-priority findings

#### F-16 — Dependency manifest exists, but it is an environment freeze rather than a focused project manifest

Severity: **Low**

The original “no requirements” suspicion is not correct. Repository-root
`requirements.txt` exists and pins the requested
`ansys-fluent-core==0.38.0` and `ansys-pyensight-core==0.11.6`, along with
NumPy, pandas, matplotlib, Pillow, and many transitive/Jupyter packages.

There is no `pyproject.toml`, declared Python version, pytest dependency, lint
configuration, or separation between runtime and development dependencies.
The large exact freeze may be useful for reproducing one workstation, but it
does not communicate the minimal supported environment.

#### F-17 — No tests or project documentation

Severity: **Medium**

No test files or test configuration are tracked. High-value pure logic already
exists for naming, config validation, path safety, inventory classification,
CSV validation, residual parsing, plateau detection, and output selection.
There is also no root README explaining entry points, config precedence,
campaign safety, or which post-processing orchestrator is authoritative.

#### F-18 — The streamline exporter is a tracked zero-byte stub

Severity: **Low**

`01_Scripts/post_processing/04_pyensight_streamline_export.py` is tracked and
empty. It should either become an explicit documented placeholder that exits
with a clear “not implemented” status, or be removed in a separately approved
cleanup. Leaving a runnable-looking zero-byte entry point silently succeeds.

#### F-19 — Archive and date-stamped entry points encourage divergence

Severity: **Low**

The archive scripts are not used by active drivers, but they are full copies.
Date-stamped worker names make each new revision likely to become another copy.
Keep existing names as compatibility entry points, but move reusable
implementation behind stable modules rather than creating another dated copy.

## Approval checkpoint

Phase 2 ends here. No runtime source, UDF, Scheme, geometry, config, or output
schema has been changed. Phase 3 should begin only after the owner selects and
approves specific plan items and explicitly decides whether items 5-7 may wait
until the active 24-case campaign has completed.

## Phase 3 — Verified on production data (2026-07-18)

Read-only checks against the server's actual campaign output, captured while the
24-case campaign was still running on **pre-fix** code. WSL-side SAFE-slice
commits (`F-01` parser fix, `F-02` inventory defaults, `F-03` status inference,
`F-03` `08` partial-export exit, `F-03` `03` default fields) landed before this
verification run but had not yet been deployed to the server at inspection time.

### Verified on production data (2026-07-18)

- **F-01 CONFIRMED:** All 15 post-processed mesh-qualified cases
  (`Sin_SL` / `Sin_ST`, `*__mesh_max100_min006_cpg3_bl3`) have
  `expected_outlet_gauge_pressure = 6000000.0` in
  `post/reports/pressure_report.csv` — including the 4 MPa and 8 MPa cases
  (not only the accidental 6 MPa matches). **Remediation after deploy:** re-run
  report extraction for those 15 cases so corrected operating metadata is written.
- **F-02 LATENT (no corruption):** Newest `_inventory/case_inventory.csv` is
  dated 2026-07-04, before the campaign started. All 93 rows have
  `max_iter_target=2000` (default confirmed in the wild), 0 mesh-qualified rows,
  0 `CONVERGED`-without-evidence rows. The fix landed before the bug could
  mislabel anything.
- **F-03 #4 CONFIRMED:** 46 cases have `contour_export_status.json` with
  `summary = {success: 3, warn: 1, failed: 0}`; one case
  (`Diamond_Spacer/u0p1_p4M__attempt_20260705_161718`) has `failed: 4`. The
  `batch_postprocess_results.csv` from the last orchestrator run records
  `pyensight_contour_stage_status = SUCCESS` for all its rows — exactly the
  masking the commit-3 JSON-inference fix addresses.
- **Shear status JSONs:** All 45 directories with shear PNGs have
  `shear_contour_status.json` — no `UNKNOWN` wave expected from commit 3 on
  shear after deploy.

#### F-03 SAFE slice — closed in repo (2026-07-18)

| Commit | Change |
|--------|--------|
| `3c14ab7` | `03` `DEFAULT_FIELDS` aligned with inventory basic set |
| `d49f4fe` | `08` nonzero exit on partial export |
| `4d4d85a` | `06` JSON stage-status inference + missing-inventory exit 2 |

Deferred within F-03: `03` / `03b` WARN → nonzero exit (#1, #2).

See **F-20** for the systematic `cp_inlet` bulk-average WARN observed across all
46 production contour status JSONs.

## Verification of the six suspected issues

1. **Duplicated solver drivers:** partially confirmed. `07` duplicates launch,
   path, reporting, and convergence infrastructure, but its continuation
   workflow is materially different from the normal solver worker. Extract
   seams; do not merge the workflows wholesale.
2. **Post-processing copy/paste:** confirmed with corrections. Config/path
   helpers recur across `02/03/03b`; PyEnSight loading recurs in `03/08`;
   naming logic is in batch/orchestrator scripts, not all three exporters.
   `01_batch_report_extract.py` substantially overlaps `06`.
3. **Inconsistent config:** confirmed, with at least six variants. Raw
   `setattr` is present in the active run and report workers.
4. **Physical constants/timeouts:** current physical values agree, but drift is
   unenforced and more UDF constants are duplicated than listed. Timeout
   fallback literals do differ (300 versus 600).
5. **Silent error handling:** confirmed at the success-contract level. Many
   broad exceptions are legitimate API fallbacks, but partial/missing requested
   outputs can still produce a successful process/stage.
6. **Dependencies/tests/stub:** corrected in part. A root `requirements.txt`
   exists; there are no tests or project metadata; `04` is a tracked zero-byte
   stub.

## Phase 2 — Prioritized improvement plan

Ranking favors impact divided by behavior-change risk. Items 1-4 are suitable
for the first approved implementation. Items touching the live solver campaign
or output values have explicit gates.

### 1. Add characterization tests and pure shared primitives

Priority: **1 — very high impact / very low runtime risk**

Changes:

- Add `tests/` with pytest tests for current plain and mesh-qualified naming,
  config precedence/validation, Windows-path refusal, case path resolution,
  inventory convergence classification, and residual parser/plateau helpers.
- Add a small `01_Scripts/_pipeline_common.py` for case-name parsing/building
  and path construction, and
  `01_Scripts/post_processing/_post_common.py` for Python-config loading,
  config access, safe path handling, and the PyEnSight load fallback.
- Define a post-load verification contract for the PyEnSight fallback so a
  case-only call is not considered successful until expected result variables
  are visible.
- Initially migrate only duplicate pure helpers in
  `02_pyfluent_field_check.py`, `03_pyensight_contour_export.py`,
  `03b_pyfluent_shear_contour_export.py`, and
  `08_pyensight_extra_figures.py`. Keep each entry point and its CLI unchanged.
- Do not force `08` onto `00_post_config.py`, and do not give `03b` a PyEnSight
  adapter it does not use.

Verification without Ansys:

- pytest with fake config modules and fake PyEnSight sessions;
- AST/compile checks with imports of pure modules only;
- golden tests for all current filenames and `03_Results` paths;
- monkeypatched platform tests for `C:/...` and `C:\...` guards.

Estimated diff: **450-700 lines** including tests; net production reduction
expected after duplicate removal.

### 2. Establish checked physical-constant parity

Priority: **2 — high impact / low risk**

Changes:

- Add one Python constants module under `01_Scripts/`.
- Make Python config/post-processing consumers reference it without changing
  numeric values.
- Add a check-only generator/parser that verifies the checked-in
  `260612_RO_UDF.c` defines and `cff_wall_shear_rate.scm` expression match the
  canonical values.
- Add tests for exact constants, units metadata, C macro parity, Scheme
  viscosity parity, and UDM index parity.
- Add `operating_pressure` to the post config at its unchanged current value,
  verify loaded CFF viscosity against configured `mu`, and document which
  material values are template-owned rather than applied by Python.
- Do not change UDF formulas, Scheme computed values, or geometry files.

Verification without Ansys:

- pure text parsing and numeric equality tests;
- golden generated snippets compared to checked-in C/Scheme text;
- static assertion that all current values remain unchanged.

Estimated diff: **180-300 lines**.

### 3. Centralize config merging with allowlists and validation, preserving interfaces

Priority: **3 — high impact / low-to-medium risk**

Changes:

- Preserve `PYFLUENT_RUN_OVERRIDES`, `PYFLUENT_POST_CONFIG`, and
  `PYFLUENT_POST_OVERRIDES`.
- Replace raw module mutation loops with shared merge functions that reject
  unknown keys, reject booleans as numbers, coerce only explicitly permitted
  types, and validate cross-field constraints.
- Apply the same validation whether the base config is default or selected by
  environment path.
- Define and test precedence as base Python config < env JSON < CLI.
- Change `06` to use the existing base-config-plus-JSON mechanism instead of
  generated Python config only after a compatibility snapshot proves the
  report worker receives byte-equivalent values. Retain a compatibility option
  for generated config during transition.

Verification without Ansys:

- table-driven tests for every accepted campaign override;
- rejection tests for unknown/misspelled keys and invalid types/ranges;
- serialized-env round trips;
- snapshots of resolved config for all 24 active cases.

Estimated diff: **350-550 lines**.

### 4. Make post-processing status reflect requested-output completeness

Priority: **4 — high impact / medium risk**

Changes:

- Parse `contour_export_status.json` and `shear_contour_status.json` in `06`
  after each worker exits.
- Keep worker compatibility fallbacks, but distinguish `SUCCESS`, `PARTIAL`,
  `WARN`, and `FAILED` in orchestrator records.
- Verify every requested field/side has its expected file before stage success.
- Treat a missing/unreadable inventory as a non-successful batch invocation.
- Preserve existing CSV/JSON columns and filenames; add strictness only as an
  opt-in flag initially, or append fields only with explicit schema approval.
- Give `08` a machine-readable manifest before integrating it into `06`.

Verification without Ansys:

- fixture status JSONs for success, missing variable, partial side, malformed
  JSON, stale PNG, and failed export;
- mocked subprocess return codes;
- golden orchestrator result records and suggested actions.

Estimated diff: **250-450 lines**.

### 5. Correct naming, artifact, and convergence classification bugs

Priority: **5 — very high impact / medium behavior risk; explicit approval gate**

Changes:

- Parse only the base portion before `__<mesh>` when deriving operating values,
  while preserving the full case folder/name.
- Reuse that parser in `06`, `01_batch_report_extract.py`,
  `batch_solver_sweep.py`, and the `07` matrix filter.
- Make missing final case/data artifacts fail the solver worker instead of
  warning and returning zero; characterize the existing setup/final write
  contract first.
- Stop inferring `CONVERGED` from output-file/report presence alone.
- Pass campaign max iterations into inventory or read it from a run manifest;
  do not silently use 2,000 for the 1,000-iteration campaign.
- Preserve existing labels/schemas, but expect corrected metadata and case
  classifications to change values.

Verification without Ansys:

- tests for every active campaign name and pressure;
- all 24 cases resolve to the expected exact velocity/pressure;
- mocked final-write checks cover missing case, missing data, and complete pair;
- synthetic log/file matrices covering converged, max-iteration, failed,
  incomplete, and unknown states;
- before/after inventory diff reviewed before adoption.

Estimated diff: **220-380 lines**.

This item intentionally changes incorrect report metadata and classification
values. It must not be applied to active outputs without explicit approval and
a migration/re-inventory decision.

### 6. Add durable per-case run manifests and stronger skip diagnostics

Priority: **6 — high impact / medium risk**

Changes:

- Write a new per-case manifest containing resolved config, exact case
  parameters, source revision, dependency/Ansys versions, input paths, worker
  return status, convergence status, and output file metadata.
- During the first release, do not change skip decisions; report whether an
  existing file pair is complete, stale, or unverifiable.
- After campaign completion, optionally make a valid completion manifest the
  skip criterion and add an opt-in worker wall-clock timeout.

Verification without Ansys:

- manifest schema and atomic-write tests;
- interrupted/stale/zero-byte output fixtures;
- compatibility tests proving current outputs are untouched.

Estimated diff: **300-500 lines**.

### 7. Reduce `07_batch_solver_rerun.py` by extraction after the campaign

Priority: **7 — medium-to-high impact / high risk**

Changes:

- Reuse common case paths, CSV/JSON I/O, launch-kwargs construction, residual
  parsing, and typed result helpers.
- Split recovery strategy algorithms from the CLI/orchestration layer.
- Require readback for settings/TUI fallbacks that are necessary for a
  strategy; record optional failures without claiming application.
- Keep continuation-specific loading, staged writes, backup, and promotion in
  the rerun workflow. Do not route it through normal initialization.

Verification without Ansys:

- existing pure residual/plateau/strategy tests with fake solver settings;
- golden plan/result CSV/JSON schemas;
- dry-run CLI snapshots;
- no live promotion tests.

Estimated diff: **900-1,500 lines moved/edited**, with a substantial net
reduction in the entry point. Do this in several commits, not one.

### 8. Clarify packaging, dependencies, documentation, and the stub

Priority: **8 — medium impact / low risk**

Changes:

- Keep the current requirements freeze until the campaign environment is
  captured; add a small development requirements file with pytest.
- Add `pyproject.toml` only for tool configuration and Python-version metadata,
  without packaging the numerically named script directories.
- Add a root README documenting entry points, config precedence, output/status
  meanings, Windows-only live execution, and no-license test commands.
- Turn `04_pyensight_streamline_export.py` into an explicit nonzero
  “not implemented” placeholder or remove it in a separately approved cleanup.

Verification without Ansys:

- clean-environment dependency resolution check where available;
- pytest collection without Ansys imports/licenses;
- CLI help/static checks for documented entry points.

Estimated diff: **180-300 lines**.

## Suggested commit sequence for approved implementation

1. `test: characterize naming config and path behavior`
2. `refactor: extract shared pure pipeline helpers`
3. `refactor: share post-processing config and load fallbacks`
4. `test: enforce physical constant parity`
5. `refactor: validate environment config overrides`
6. `fix: parse mesh-qualified operating conditions` — only with explicit
   approval for corrected output values
7. `fix: fail incomplete final artifact writes`
8. `fix: separate inventory completion from convergence` — only with explicit
   approval for reclassification

No implementation commit should mix output-semantic fixes with helper
extraction. Each commit should pass pure-Python tests and preserve entry-point
names and external interfaces.
