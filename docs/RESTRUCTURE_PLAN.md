# Repository restructure — working plan

**Status: complete.** Steps 1–10 are done. Remaining sections are reference:
id grammar, path rules, manifest schemas, settled decisions, and the silent-risk
list. Do not infer new work from the historical step detail in section 10.

Live campaign facts (31 geo_ids / 31 meshes / 279 solver runs, sinusoidal
rebuild, curvature gate, `REF_empty`, flux decomposition, `u_mean_ms`
mislabeled) are in `docs/GEOMETRY_DESIGN.md`, `docs/MESH_LANDSCAPE.md`,
`docs/metrics_conventions.md`, `docs/POSTPROCESSING_MAP.md`, and
`docs/PIPELINE_MAP.md`. Section 7 rows superseded since this plan closed:
`Empty` is now `REF_empty`; CAD exists for all four families plus empty;
the whitelist is 31 (no `_f320`).

**Branch:** `feat/diagnostics-and-mesh-ledger`

Server checkpoint after the artifact rename (`1ec3263`): full remesh of
`D2450_a45` / `max085_min006_cpg5_bl4_peel2` produced 796,009 cells, ortho
0.102087, AR 62.7715, skew 0.67063399 — matching the archive ledger. New
filenames throughout. The restructure is physics-neutral.

---

## 1. Status

| step | description | state |
|---|---|---|
| 1 | Package scaffold: `pyproject.toml`, `src/ro/__init__.py`, `requirements-dev.txt` | **DONE** — `580034e` |
| 2 | `git mv` seven helpers to `src/ro/`, leave import shims | **DONE** — `293f8e2` |
| 3 | Switch `from _x` → `from ro.x` everywhere | **DONE** — `b50026e` |
| 4 | Delete shims; stop putting `01_Scripts` on `sys.path` | **DONE** — `229e469` |
| 5 | Add `src/ro/paths.py` + `src/ro/manifest.py` + their tests | **DONE** — `fc7eb4f` |
| 6a | Route every path construction through `ro.paths` builders | **DONE** — `5c3d81f` `25e68ac` `af723e1` `6a4be78` `b8b67f7` |
| 6b | Manifests: write on creation, read in post; layout from manifest | **DONE** — `f1d0eb4` `69e6a78` `fd17b2b` `a4c6e0b` `0b9d76f` `1bd6159` `01e1941` |
| 6c | CLI flags → `--family --geo-id --mesh-id --run-id` | **DONE** — `18a5a2c` |
| 6c+ | Artifact names `{geo_id}_{run_id}` / `{geo_id}_{mesh_id}` | **DONE** — `1ec3263` |
| 7 | Directory reshuffle by `git mv` (7a templates/udfs, 7b configs, 7c scripts, 7d docs) | **DONE** — `dbb9af1` `fc75641` `869680f` `0c3f0e5` |
| 8 | Drop numeric prefixes on orchestration scripts | **DONE** — `2abc69e` |
| 9 | Remove the name-keyed domain-layout registry | **DONE** — `5beb040` |
| 10 | Docs: AGENTS.md, DEPLOY_RUNBOOK.md | **DONE** this commit |

Related: peel token mandatory in `mesh_id` (`fffd5fe`); refuse remesh while
runs cite `mesh_sha256` (`cca62b2`); candidate CSV is manually promoted
(`47bf993`).

### Test baseline

`python -m pytest -q` → **784 passed** on WSL (0 skipped). Windows without
symlink privilege: **783 passed, 1 skipped**.

---

## 2. Layout

### Code repo (git-tracked)

Server path is `C:\pyfluent`. The `My_CFD_Project/` level is gone.

```
<repo>/
  pyproject.toml
  requirements.txt              # Ansys/jupyter pins, no pytest
  requirements-dev.txt          # -r requirements.txt + pytest>=8
  src/ro/                       # installable package
  scripts/                      # entry points (flat; post_processing/ flattened in)
  configs/                      # run_config, batch_config, post_config, batch_post_config
  templates/                    # was 01_Templates
  udfs/                         # was 02_UDFs — FILENAMES UNCHANGED
  tests/                        # already at repo root
  docs/                         # AGENTS.md, DEPLOY_RUNBOOK.md, REVIEW.md, archive/
  notebooks/
```

`00_Geometries/` and `03_Results/` are not in the repo. CAD lives under
`RO_DATA_ROOT/geometries/`; run artifacts under `RO_DATA_ROOT/runs/`.

### Data root (git-external, `RO_DATA_ROOT`, `C:\ro_data` on the server)

```
geometries/{family}/{geo_id}/
meshes/{family}/{geo_id}/{mesh_id}/
runs/{family}/{geo_id}/{mesh_id}/{run_id}/
archive/                        # frozen legacy; NO code reads this
inventory/                      # aggregate CSVs, ledger
```

`case_inventory.py` writes `inventory/rerun_candidates.csv`.
`batch_solver_rerun.py` reads `inventory/active_solver_rerun_candidates.csv`.
The latter is **manually promoted** from the former (current-matrix only).
Nothing in the repo writes or refreshes `active_*`.
A missing file is an error; a header-only file is the legitimate empty queue.
The fact previously lived only in `REVIEW.md` backlog #4.

Historical run data has been moved to `archive/` and will **not** be used for the
paper. Everything is being re-run under the new scheme. There is no backward
compatibility requirement — do not preserve old behaviour "just in case."

---

## 3. ID grammar

```
family  = diamond | ml | pillar | sin | empty
geo_id  = D2450_a45
mesh_id = max085_min006_cpg5_bl4_peel2 (no "mesh_" prefix; peel is mandatory)
run_id  = u0p2_p6M                 # campaign form (parabolic)
          u0p2_p6M_plug            # optional letter-led label; not parsed
```

The `_plug` suffix is optional. Peel is mandatory because two peel variants
coexisted and a bare name meant "peel on". Inlet BC is not like that:
parabolic is the campaign standard, plug is a one-off comparison. Do not put
`_para` on the 495 matrix run_ids. `inlet_bc_type` in the run manifest is
authoritative; nothing reads the BC from `run_id`. `MATRIX_BASE_CASE_RE` in
`solver_common` stays `^u\d+p\d+_p\d+M$` so 07 does not treat `u0p2_p6M_plug`
as a matrix case.

Compiled once in `src/ro/paths.py`, validated at every builder call:

```python
FAMILY_RE  = re.compile(r"^(?:diamond|ml|pillar|sin|empty)$")
GEO_ID_RE  = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")   # <=64 chars
MESH_ID_RE = re.compile(r"^max\d{3}_min\d{3}_cpg\d+_bl\d+_peel\d+$")   # min0006/bare peel must FAIL
RUN_ID_RE  = re.compile(r"^u\d+p\d+_p\d+M(?:_[a-z][a-z0-9]*)?$")
_GEO_ID_FORBIDDEN = re.compile(r"(?:_brg\d+|_\d+c)(?:_|$)")            # loud reject
```

`_GEO_ID_FORBIDDEN` exists so old folder names (`D2450_a45_7c_brg110`) cannot be
reused by accident — cell count and bridge radius belong in the manifest.

### Builder-only rule

`geometry_dir()`, `mesh_dir()`, `run_dir()` are the **only** permitted way to form
a geometry, mesh, or run leaf. Any call site doing `data_root() / "runs" / ...` by
hand is out of policy. Typos must raise `ValueError`, never silently create a
parallel directory that a later glob picks up as a real case.

### Path is for humans; manifest is for code

Never parse a directory name to recover a parameter. Glob `**/manifest.json` to
*find* leaves; read parameters from the JSON. This is what makes it safe to rename
a path component later.

---

## 4. Path resolution — `src/ro/paths.py`

```
project_root()
  1. PYFLUENT_PROJECT_ROOT if set and non-empty. Path(value), NOT resolve()
     (a relative override stays cwd-relative; frozen env contract).
     Empty string is treated as unset.
  2. Else walk upward from Path(__file__).resolve() until pyproject.toml is
     found. RuntimeError if no marker.
     Do NOT use a fixed parents[N] — that is the bug class this replaces.

data_root()
  RO_DATA_ROOT if set, non-empty, and ABSOLUTE. Otherwise ValueError.
  Unset raises. Relative raises. It NEVER defaults to project_root().

templates_dir()   project_root() / "templates"
udfs_dir()        project_root() / "udfs"
geometries_root() data_root() / "geometries"
meshes_root()     data_root() / "meshes"
runs_root()       data_root() / "runs"
```

There is **no** `results_root()` and no `03_Results`. Do not keep an alias.
There is **no** `honor_env=False` escape hatch — one resolution order, no
exceptions, or the six-mechanism mess returns.

Env is read on every call, never cached. CLI flags are applied by callers after
these functions return, and still win.

### Import-time constraint

`data_root()`, the three `*_root()` functions, and the builders must **never** be
called at module import time. Raising on unset `RO_DATA_ROOT` is correct, but a
module-level `DEFAULT_RESULTS_ROOT = results_root()` would make `script.py --help`
fail on the dev machine and break import-only tests.

Call them inside `main()`, or resolve an argparse default after parse.

Add a test that imports every module in `scripts/` with `RO_DATA_ROOT` unset and
asserts no exception.

### Importability

- **pytest:** `[tool.pytest.ini_options] pythonpath = ["src"]` — suite runs on a
  fresh venv without an editable install.
- **CLI / subprocess workers:** require `pip install -e .`. Configs are loaded by
  `importlib` from `configs/`, which does not put `src/` on `sys.path`. Do not add
  `sys.path.insert` to scripts. There is no `ro.ensure_importable()` helper;
  `ModuleNotFoundError: No module named 'ro'` is the failure.

---

## 5. Manifests — `src/ro/manifest.py`

Path is always `mesh_dir(...)/manifest.json` or `run_dir(...)/manifest.json`,
formed via builders.

### Mesh manifest (`schema_version: 1`)

Required fields:

```
family, geo_id, mesh_id
spacing_code, attack_angle_deg
filament_d_m                    # 4.0e-4 for the current diamond family
bridge_radius_m                 # 1.10e-4 — REQUIRED even though absent from geo_id
overlap_m                       # 0.0 for current tangential-contact diamond
n_active_cells, n_buffer_in, n_buffer_out, cell_length_x_m
buffer_length_in_m, buffer_length_out_m   # metres; independent of pitch
# cell_length_x_m is active pitch only. total length is
# buffer_in + n_active * pitch + buffer_out, not n_total * pitch.
membrane_wall_base_names        # e.g. ["wall_top_mem", "wall_bottom_mem"]
buffer_wall_base_names          # generation-dependent; see note below
n_lead_excluded, n_trail_excluded
max_size, min_size, cpg, bl, peel
ortho_min, AR_max, skewness_max, skewed_face_fraction, cell_count
inlet_profile_G                 # NULLABLE at creation; see section 6
mesh_sha256
created_utc, generator_version
```

The four wall-name and exclusion fields are what allowed the name-keyed
`GEOMETRY_LAYOUT_REGISTRY` to be deleted in step 9. Without them, post-processing
still has to key on `(geo_name, mesh_case_name)`.

Buffer wall zone names are generation-dependent: legacy meshes use
`wall_top_buffer`, current ones use `wall_top_buffer_in` / `_out`. That is exactly
why the names are recorded per-mesh rather than assumed.

Quality fields may be `null` only *before* `mesh_sha256` is set. Once the
`.msh.h5` exists, quality and sha are required — loudly.

`peel` is mandatory in `mesh_id` and remains an explicit numeric manifest field.
Writers validate that the `_peel<N>` token equals the manifest value; readers
still use the manifest rather than recovering the value from the directory name.

### Run manifest (`schema_version: 1`)

```
family, geo_id, mesh_id, mesh_sha256   # copied from the mesh manifest at solve start
run_id
u_mean_ms                              # NULLABLE until inlet_profile_G is known
p_gauge_pa                             # NUMERIC. Never recovered from run_id.
u_target_ms                            # REQUIRED; the value patched into the UDF
inlet_bc_type                          # "parabolic" is the standard; "plug" is legacy
udf_version                            # e.g. "260816_RO_UDF.c"
solver_settings { max_iterations, residual_target, operating_pressure }
stop_reason
created_utc
```

At solve start, `u_target_ms` is always known and written. For a parabolic inlet,
`u_mean_ms` stays null until the UDF-emitted `inlet_profile_G` is parsed; the same
lazy fill writes `u_mean_ms = u_target_ms / inlet_profile_G`. For a plug inlet,
`u_mean_ms` is known without `G` and may be written as `inlet_velocity_value`.
Neither value is inferred from `run_id`.

`stop_reason` is written as `RUNNING` at solve start, then replaced by the same
final stop-reason enum emitted in the solver transcript. A manifest left at
`RUNNING` after the worker exits means the run crashed or was killed; readers
must not treat it as pending or successful.

`06.parse_case_operating_values` recovered `u` and `p` by regex on the
case name. That function was **deleted** in step 6b, not adapted.

### Reader/writer API

```python
class ManifestError(ValueError): ...

write_mesh_manifest(mesh_directory, payload) -> Path   # validate, atomic replace
read_mesh_manifest(mesh_directory) -> dict
write_run_manifest(...), read_run_manifest(...)
iter_mesh_manifests() -> Iterator[tuple[Path, dict]]
iter_run_manifests()  -> Iterator[tuple[Path, dict]]
```

**Stale-path check (loud):** the ids inside the JSON must equal the directory
components, and `mesh_dir(family, geo_id, mesh_id) == mesh_directory.resolve()`.
A folder moved by hand with an old JSON inside must fail, not be trusted.

**Overwrite guard (loud):** `write_mesh_manifest` raises `ManifestError` if a
manifest already exists at the target and its CAD/mesh parameters differ from the
incoming ones. Same for runs.

This rule still covers parameters that change results but remain absent from the
path, including `overlap_m` and `bridge_radius_m`. `peel` is both path-visible and
guarded as a manifest parameter, so an inconsistent token/value pair is rejected
before any overwrite.

**No fallback anywhere.** A leaf without a valid manifest is refused, not
processed with defaults.

---

## 6. `inlet_profile_G`

**Status: done.** Marker UDF `c16ac68` (`260822_RO_UDF.c`); parse-and-assert
`c9510af`. Lazy-fill and comparison are both server-verified on
`D2450_a45` / `max085_min006_cpg5_bl4_peel2`:

| run | path | result |
|---|---|---|
| `u0p1_p6M` | lazy fill | mesh `inlet_profile_G` **1.00360939613**, `u_mean_ms` 0.0996403584757259 |
| `u0p3_p6M` | comparison | matches transcript (relative 0), no refill, `u_mean_ms` 0.29892107542717766 |

That G is the measured quadrature for this mesh only. Do not copy it onto
another `mesh_id`. `U_MEAN = U_TARGET / G` is closed.

### Why

The inlet BC is a self-normalizing parabolic profile. `U_MEAN = U_TARGET / G` where
`G` is the discrete quadrature of the unit parabola over the inlet faces:

```
G = Σ 6·η_i·(1 − η_i)·dA_i / Σ dA_i,    η = (z − z_bottom) / H
```

On this D2450 peel2 mesh `G = 1.00360939613`, giving `U_MEAN = 0.199281` for a
0.2 m/s target — a +0.3609% discrete-integration bias. That constant is
**mesh-specific**. Reusing it on a mesh with different inflation or
z-discretization silently corrupts LMH with no error at all. Cross-geometry LMH
comparisons are the entire point of the campaign, so this must be caught
mechanically.

**`G` is the mesh invariant, not `U_MEAN`.** `U_MEAN` depends on the target
velocity and therefore differs across the 0.1 / 0.2 / 0.3 sweep on the same mesh.
Store `G` in the mesh manifest; store `u_target_ms` in the run manifest.

### Design — solver-side, lazy fill, no second Fluent start (as implemented)

`ensure_inlet_G` in `260822_RO_UDF.c` computes `G` at runtime. The solver parses
that value rather than recomputing it mesh-side, so the asserted number is the
one the profile actually used.

1. **Dated UDF.** `260822_RO_UDF.c` (not an in-place edit of `260816`).
2. **Marker.** `Message0("RO_UDF_INLET_PROFILE_G=%.12g\n", G)` after G is
   accepted; not on the `G = 1` fallback. Probe plus `DEFINE_INIT` reset plus
   the first profile-hook call can emit the token twice; all `fluent-*.trn`
   occurrences must be identical strings or the solver raises.
3. **Timing.** The execute-on-demand probe runs after every `libudf` load
   (always-on). Parse-and-assert runs after the probe and **before** `iterate`.
   After iterate, tokens are collected again so a mid-session change cannot pass.
4. **Parse.** `fluent-*.trn` only, never `solver_log_*.txt`. Missing marker is
   an error.
5. **Lazy fill.** Null `inlet_profile_G` is written once. Later runs compare at
   1e-6 relative and abort before iterate on mismatch. Never overwrite.
6. **`u_mean_ms`.** Parabolic finalization writes `u_target_ms / G`. Plug writes
   `u_target_ms` at start.

The first run on a new mesh has nothing to compare against. That is acceptable:
the failure being prevented is reuse of one mesh's constant on a different mesh,
which lazy fill makes structurally impossible since every mesh acquires its own.

---

## 7. Decisions already settled

Do not reopen these.

| item | decision |
|---|---|
| `D0817_*` | `family=diamond`. It is a regular spacing code in the 9-case matrix (D2450/D1225/D0817 × a30/a45/a60), not a diagnostic. No `diag` family. |
| `Sin_ST`, `Sin_SL`, `Hole_Pillar`, `Multi_Layer_*`, `Diamond_ov020`, `D2450_a45_ov060` | **Archive-only names.** No code reads them. Do NOT widen any regex or add schema fields to accommodate them. New pillar and sinusoidal geometries will be regenerated under the same parametric scheme as diamond. See the channel-geometry note below. |
| `Empty` | Stays active. `family=empty`, `geo_id=Empty`. |
| bridge radius | Fixed at 1.10e-4 for all current diamond geometries. Not in `geo_id`; required in the manifest. |
| overlap | Not in `geo_id`. Required in the manifest as `overlap_m`. |
| numeric script prefixes | Dropped in step 8. They do not match execution order (`06` calls `01` and `03`). |
| `mesh_` prefix on `mesh_id` | Dropped. Canonical was `mesh_max085_...`; target is `max085_...`. Every builder and `assert_mesh_case_name_matches` changes. |
| `results_root()` | Deleted, not aliased. |
| `RO_DATA_ROOT` unset | Raises. Never falls back to the git tree. |
| dated filenames | `meshing_code_260616.py`, `solver_code_260616.py`, and all dated `*_RO_UDF.c` keep their names. Directory `git mv` only. |
| pytest dependency | `requirements-dev.txt`, not `requirements.txt` (the latter is the server's Ansys/jupyter pin list). |
| `max_iterations` | Code is consistently 2000. `07`'s 3000 is a different knob (rerun budget). |
| plug vs parabolic | Closed on `D2450_a45` / `max085_min006_cpg5_bl4_peel2` / `u0p2_p6M` vs `u0p2_p6M_plug`. See below. |
| entrance decay | Tracks cell count, not millimetres. Campaign `n_lead_excluded = 3` for all nine diamonds. See below. |
| `periodic_after_surface_mesh` | Campaign default **True**. `D2450_a45` was meshed with False and is not remeshed. Mixed-order by design. See below. |
| surface-size grid independence | **Not closed.** The 0.44% LMH / 7.1% CP-excess numbers are bl4 vs bl6 at fixed `max085`, not an `m_max` sweep. See below. |

### Plug vs parabolic — `D2450_a45` / `u0p2_p6M`

Same mesh, same operating point, `use_inlet_velocity_profile` True vs False.
Both stopped at iteration 301, the first legal QoI window (not a detected
plateau). At \(u=0.2\) that window already matched the 379-iteration
residual-converged field to 6 sig figs, so the comparison holds. Parabolic
is the campaign leaf; plug is `u0p2_p6M_plug`. Every headline metric agrees
within 0.02%:

| metric | parabolic | plug | relative |
|---|---:|---:|---:|
| `lmh_udm_avg` | 25.8773 | 25.8798 | +0.010% |
| `lmh_mass_balance` | 25.8845 | 25.8876 | +0.012% |
| `cp_inlet_avg` | 1.05827 | 1.05817 | −0.009% |
| `cp_inlet_max` | 1.38812 | 1.38791 | −0.015% |
| `pp_pressure_drop_periodic_per_m` | 33094 Pa/m | 33099 Pa/m | +0.016% |
| `cm_avg` | 632.53 | 632.47 | −0.009% |

CP was the last open question from this comparison. Inlet BC does not change
the scored CP: the 1-cell inlet buffer plus `lead=3` puts the evaluation
window `(1+3)×0.003465 m = 13.86 mm` from the inlet, more than 10 mm and far
beyond the laminar entrance length at this Re. Per-cell ΔP on this pitch
already flattened by cells 4–7. Campaign BC stays parabolic; the plug leaf
is a one-off.

### Entrance decay — cell count, not millimetres

Archive `D0817_a45_21c_brg110` / `entrance_para_u0p2_p6M` (pitch 1.155 mm,
260810 UDF, usable for dP) vs campaign `D2450_a45` (pitch 3.465 mm).
Active cell 1 is global `cell_2`. Plateau on the 21c is 129.9 Pa
(cells 6–21).

| active cell | x (mm, from membrane start) | dP (Pa) | vs plateau |
|---|---:|---:|---:|
| 1 | 1.155 | 332.6 | +156% |
| 2 | 2.310 | 140.0 | +7.8% |
| 3 | 3.465 | 137.3 | +5.7% |
| 4 | 4.620 | 132.6 | +2.1% |
| 5 | 5.775 | 129.9 | +0.0% |

D2450 decayed inside 3 cells (+26% / +9.4% / +2.5% vs its plateau). Against
the 21c (4–5 cells to plateau):

| | D2450 | 21c | ratio |
|---|---:|---:|---:|
| cells to plateau | 3 | 4–5 | < 1.5× |
| distance to plateau | 10.4 mm | 5.2 mm | 2× |
| pitch | 3.465 mm | 1.155 mm | 3× |

Decay follows spacer cells, not a fixed millimetre window. There is a weak
secondary trend (3 vs 4–5 cells to the plateau). That is not used to vary
lead: `lead=3` discards active cells 1–3, so the first scored cell on the
21c is active 4 at +2.1%, which meets the same ~2% bar as D2450's discarded
cell 3 (+2.5%). D2450's first scored cell is already on the plateau.

**Campaign `n_lead_excluded = 3` on all nine diamonds.** One integer, matching
cell-count scaling and the a60 five-cell ceiling. Interpolating lead against
pitch would freeze a six-geometry guess into overwrite-guarded manifests.

After each new mesh, the first `u0p2_p6M` extract already writes every
`pp_pressure_drop_cell_N`. Check active cells 3–6 against that case's
plateau. If the first scored cell exceeds ~2.5%, remesh **that** geo at
`lead+1` (new `mesh_id`). `pp_pressure_drop_cell2_over_cell3` is hardcoded
to global cells 2 and 3 (active 1 / active 2) and measures the entrance
spike, not the tail; do not use it to pick lead.

Mesh order for the remaining eight: **D0817_a45 first** (pitch already
measured on the 21c), then **D2450_a60** (five active cells), then the rest.

### Periodic task order — mixed on purpose

`periodic_after_surface_mesh` defaulted to False (Manual, before surface mesh)
when the flag was added. That is the path that built hashed `D2450_a45` /
`max085_min006_cpg5_bl4_peel2`. The 21c diagnostic at 1.155 mm pitch used
True (Automatic, after surface mesh) and completed the volume mesh.
Production `D0817_a45` surface max skewness was 0.956 on False and 0.743 on
True.

The campaign default is now True for every **unmeshed** geo. `D2450_a45` is
not remeshed: skip-existing plus the hash overwrite guard refuse in-place
replacement while runs cite `mesh_sha256`, and False already produced a
valid volume mesh on that CAD. Remeshing it under True would be a new
`mesh_id` and a new solve set, not a free consistency fix. The ledger
column `periodic_after_surface_mesh` is how the mixed-order campaign stays
auditable.

### Grid convergence — wall-normal is not surface size

`AGENTS.md` and the `260822_RO_UDF.c` header overstated this. The quoted
0.44% LMH and 7.1% CP-excess (header: "~0.4% against geometry-to-geometry
2.7%") compare **bl4 vs bl6 at fixed `max085`**, same operating point
(`D2450_a45` / `u0p2_p6M`), both stopping at iteration 301 (first legal
QoI window; at \(u=0.2\) that matched residual-converged 379). That is a
wall-normal (prism count / y1) check. It is not an `m_max` / surface-size
study, and it does not close grid independence for the campaign mesh.

Do not edit the dated UDF header; it is a used results file. The
correction lives here and in `AGENTS.md`. An `m_max` sweep on `D2450_a45`
(max120 / hashed max085 / max060, `u0p2_p6M` parabolic) is the surface-size
study; pin `periodic_after_surface_mesh = False` on the new meshes so they
match the hashed reference.

**When regenerating pillar / sinusoidal families.** The dated UDF hardcodes
`INLET_Z_BOTTOM`, `CHANNEL_HEIGHT`, and `INLET_AREA_EXPECTED_M2` for the current
diamond channel geometry. All nine diamond geometries share that channel, so the
constants are safe today. A Pillar or Sinusoidal family with a different channel
height would silently invalidate them — the same class of failure as reusing
`inlet_profile_G` on a different mesh. Do not fold a per-family fix into the
260822 comment/marker pass; handle it when those families are regenerated.

---

## 8. Silent failure modes

Loud failures are fine. These are the ones that produce a plausible-looking wrong
answer, listed roughly by how much damage they do.

1. **A `__file__` locator one level off that lands on a real directory.** Copying
   `SCRIPT_DIR.parents[1]` from a `post_processing/` file into `scripts/` yields the
   *parent of the repo* (`~/code/` on WSL). It exists, so nothing raises.
   Mitigation: no production `parents[N]` project-root locator survives step 6a;
   `project_root()` walks for `pyproject.toml`; grep `parents[` in `scripts/` and
   `configs/` during the reshuffle commits.
2. **`06.write_report_config`'s `project_root = results_root.parent`.** After this
   change there is no `results_root`, and `run_dir`'s parent is a `mesh_id`
   directory. "Updating" this line to `run_dir.parent` would point workers at
   `templates/` and `udfs/` under `.../meshes/.../max085_..._peel2/` — a real
   directory.
   **Delete the identity.** Pass `project_root()` and `run_dir()` as separate
   values. Prefer `PYFLUENT_POST_OVERRIDES` onto the stock `configs/post_config.py`
   over generating a `.py`.
3. **Manifest missing or stale while code falls back to dirname parsing.** Refuse
   to process a leaf without a valid manifest; refuse if the JSON ids disagree with
   the path; no registry fallback anywhere.
4. **Name-parsing leftovers.** `mesh_case_name_candidates_from_dirname`,
   `make_mesh_qualified_case_name` used for *paths*, the 2-level `geo/case` walk
   in inventory and `09`. After step 6b,
   `grep -r "__mesh_\|03_Results"` must be empty in production code. Tokens like
   `u0p2_p6M` survive only as `run_id` labels. `parse_case_operating_values` and
   `strip_mesh_suffix` are gone.
5. **Monkeypatching through a shim.** Patching a name on a shim module does not
   affect the implementation's globals. This already bit
   `test_shear_cff_mu_guard.py` in step 2. Any other test doing this is latent
   until step 4 deletes the shims — check for it during step 3.
6. **CLI still `--geo-name` / `--case-name` while disks use four ids.** Done in
   6c. Orchestrator selectors are `--family --geo-id --mesh-id --run-id`.
   Worker `--geo-name` / `--case-name` remain filename labels only.
7. **`RO_DATA_ROOT` accidentally set to the repo on the dev machine.** Tests would
   write into git. Tests use `tmp_path` only and `monkeypatch.delenv` in the
   defaults case; they never read the developer's env.
8. **Inventory exiting 0 on an empty scan.** New inventory must raise
   `NotADirectoryError` if `runs_root()` is missing, not report "Total cases: 0".
   That exact silent success is what motivated this whole restructure.
9. **pytest green without `pip install -e .`, then the server fails at
   `import ro`.** `pythonpath=["src"]` covers pytest only. Runbook step 1 on the
   server is `pip install -e .`. The failure is `ModuleNotFoundError: No module
   named 'ro'` at import. There is no extra `__main__` guard.

---

## Known limitations

### `reduction.sum_if` is broken — production uses iso_clip + surface reports

Not a restructure regression. Added in `0cb3e55` (2026-08-02); the 2026-08-11
server probe (`2bc4726`) established two independent defects in
`reduction.sum_if`:

1. Species/UDM expressions throw
   `api-checks-before-command-or-query: command/query is not active` on
   `setup/named-expressions/temp_expr_1/get-value`. PyFluent implements
   `sum_if` via that temporary named-expression path.
2. `weight="Area"` is ignored even when the call does not throw.
   `expression="1"` returned a face count (2213) where the iso-clip area is
   \(1.126\times10^{-5}\,\mathrm{m}^2\).

Production `segmented_membrane_cp_metrics` uses `results.surfaces.iso_clip`
on the membrane walls plus `surface-area` / `surface-areaavg` /
`surface-facetmax|min` reports (same pattern as
`scripts/_tmp_cell_profile.py`). Mid-plane campaign \(c_b\) uses the same
iso_clip path with `surface-massavg` on the species field. Do not reintroduce
`reduction.sum_if` — it both throws and silently ignores `weight="Area"`.

Inventory classifies on artifact presence, `stop_reason`, and a handful of
log patterns (`Expression Error UsedIn`, graphics/UDF/launch). It does not
read `segmented_cp_diagnostic_error`. Batch post `report_stage_status` is
the worker return code. Window mid-plane \(c_b\) failure is loud: extraction
raises so canonical CP is not silently replaced by `cp_inlet_avg` (L2).

---

## Known open questions

None that block the eight remaining diamond meshes. Campaign lead is
`n_lead_excluded = 3` (section 7); confirm on the first extract of each
new mesh and remesh that geo only if the first scored cell exceeds ~2.5%.

---

## 9. Working constraints

- **All development happens in WSL** (`~/code/PyFluent`). The Windows server only
  pulls. Never propose a step that requires running on the server mid-sequence.
- **Tests must never require a real data directory.** `RO_DATA_ROOT` is unset on
  the dev machine. Everything is `tmp_path` + `monkeypatch`.
- **`git mv` for every move** so history follows. Never delete-and-recreate.
- **One step per commit**, each leaving pytest green. Stop and report after each.
- **Report contradictions rather than working around them.** If something here
  disagrees with the code, say so.

---

## 10. Execution log (historical)

These are the commits that closed each step. Do not treat this section as a
todo list.

**Step 3 — imports.** `b50026e`. `from _x` → `from ro.x`. Shims stayed until
step 4.

**Step 4 — delete shims.** `229e469`. `conftest.py` keeps only `tests/` on
`sys.path` for `helpers`. 727/3 was the packaging checkpoint.

**Step 5 — paths and manifests.** `fc7eb4f`. Production did not import them yet.

**Step 6a — builders.** `5c3d81f` diagnostics; `25e68ac` workers; `af723e1`
`6a4be78` `b8b67f7` post locators and inventory under `data_root()`. Every
`parents[N]` project-root locator was deleted with the file it lived in.

**Step 6b — manifests.** Writers `f1d0eb4` `69e6a78`. `07` reads ids from the
run manifest `fd17b2b`. Inventory from manifests `a4c6e0b`. Post layout from
the mesh manifest `0b9d76f`. Leftover two-level cas/dat helpers and
`strip_mesh_suffix` deleted `1bd6159`. Report extraction refuses missing layout
`01e1941`.

**Step 6c — CLI.** `18a5a2c`. Orchestrator selectors are `--family --geo-id
--mesh-id --run-id`. Worker `--geo-name` / `--case-name` remain filename
labels, equal to `geo_id` / `run_id`.

**After 6c — artifact names.** `1ec3263`. Cas/dat are
`{geo_id}_{run_id}_final.{cas,dat}.h5`. Mesh files are `{geo_id}_{mesh_id}.msh.h5`.
Inventory and 06 refuse a glob fallback when the expected name is missing but
another `*_final` file is present.

**Step 7 — reshuffle.** `dbb9af1` templates/udfs; `fc75641` configs; `869680f`
scripts; `0c3f0e5` remaining `My_CFD_Project/` trees into `scripts/` and `docs/`.

**Step 8 — prefixes.** `2abc69e`. `07_batch_solver_rerun.py` →
`batch_solver_rerun.py`, and the rest of the orchestrators.

**Step 9 — registry.** `5beb040`. `layout_from_mesh_manifest` only.
`resolve_layout(geo, mesh)` raises.

**Step 10 — docs.** This commit. `AGENTS.md` and `DEPLOY_RUNBOOK.md` match the
tree. `REVIEW.md` is left as the July 2026 historical record.

---

## 11. Verification gate

The naming remesh checkpoint has passed (cell count and quality match the
archive ledger). The 495 figure is ~55 geometries across Diamond, Multi-Layer,
Sinusoidal, and Pillar, times nine operating points. Diamond/81 is phase one
because it is the only family with CAD today; the other families need CAD
regeneration under the same parametric scheme.

The `D2450_a45` completion check is done: LMH, evaluation-window ΔP, and CP
are physically consistent, and plug vs parabolic agrees to 0.02% (section 7).
The legacy `03_Results` tree is frozen under `RO_DATA_ROOT/archive/` and the
`_scratch` logs are archived at `archive/logs/scratch_20260820.tgz` if a
number needs to be recovered for comparison.
