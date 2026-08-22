# Repository restructure — working plan

**Audience:** an agent picking this up mid-sequence with no memory of the planning
conversation. This file is the sole source of truth for the plan. Do not infer
missing decisions; if something needed is not written here, stop and ask.

**Branch:** `feat/diagnostics-and-mesh-ledger`

---

## 1. Status

| step | description | state |
|---|---|---|
| 1 | Package scaffold: `pyproject.toml`, `src/ro/__init__.py`, `requirements-dev.txt` | **DONE** — `580034e` |
| 2 | `git mv` seven helpers to `src/ro/`, leave import shims | **DONE** — `293f8e2` |
| 3 | Switch `from _x` → `from ro.x` everywhere | **NEXT** |
| 4 | Delete shims; stop putting `01_Scripts` on `sys.path` | pending |
| 5 | Add `src/ro/paths.py` + `src/ro/manifest.py` + their tests | pending |
| 6a | Route every path construction through `ro.paths` builders | pending |
| 6b | Manifests: write on creation, read in post; layout from manifest | pending |
| 6c | CLI flags → `--family --geo-id --mesh-id --run-id` | pending |
| 7 | Directory reshuffle by `git mv` (7a templates/udfs, 7b configs, 7c scripts, 7d docs) | pending |
| 8 | Drop numeric prefixes on orchestration scripts | pending |
| 9 | Remove the name-keyed domain-layout registry | **DONE** |
| 10 | Docs: AGENTS.md, DEPLOY_RUNBOOK.md | pending |

Both commits are pushed. Working tree clean and level with origin.

### Test baseline

`python -m pytest -q` → **727 passed, 3 skipped** on WSL.

The 3 skips are the `CAMPAIGN_CASES` rows with `mesh_name is None` in
`test_solver_common_naming.py:103`. They are always skipped.

An earlier figure of **726 passed, 4 skipped** is the *Windows server* count.
`test_07_path_parity.py` has a `skipif(not _can_create_symlinks())` that passes on
WSL and skips on Windows without symlink privilege. Both counts are correct for
their host. Do not treat the difference as a regression.

---

## 2. Target layout

### Code repo (git-tracked)

Server path becomes `C:\pyfluent` (lowercase; manual server-side rename, out of
scope for the agent). The `My_CFD_Project/` level disappears entirely.

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

`00_Geometries/` and `03_Results/` leave the repo entirely.

### Data root (git-external, `RO_DATA_ROOT`, `C:\ro_data` on the server)

```
geometries/{family}/{geo_id}/
meshes/{family}/{geo_id}/{mesh_id}/
runs/{family}/{geo_id}/{mesh_id}/{run_id}/
archive/                        # frozen legacy; NO code reads this
inventory/                      # aggregate CSVs, ledger
```

`00_case_inventory.py` writes `inventory/rerun_candidates.csv`.
`07_batch_solver_rerun.py` reads `inventory/active_solver_rerun_candidates.csv`.
The latter is **manually promoted** from the former (mesh-qualified,
current-matrix only). Nothing in the repo writes or refreshes `active_*`.
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
run_id  = u0p2_p6M
```

Compiled once in `src/ro/paths.py`, validated at every builder call:

```python
FAMILY_RE  = re.compile(r"^(?:diamond|ml|pillar|sin|empty)$")
GEO_ID_RE  = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")   # <=64 chars
MESH_ID_RE = re.compile(r"^max\d{3}_min\d{3}_cpg\d+_bl\d+_peel\d+$")   # min0006/bare peel must FAIL
RUN_ID_RE  = re.compile(r"^u\d+p\d+_p\d+M$")
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
  `sys.path.insert` to scripts.
- One `ro.ensure_importable()` helper raising `ImportError` with the
  `pip install -e .` hint, called at the top of `__main__` workers only.

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

`06.parse_case_operating_values` currently recovers `u` and `p` by regex on the
case name. That function is **deleted** in step 6b, not adapted.

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

### Why

The inlet BC is a self-normalizing parabolic profile. `U_MEAN = U_TARGET / G` where
`G` is the discrete quadrature of the unit parabola over the inlet faces:

```
G = Σ 6·η_i·(1 − η_i)·dA_i / Σ dA_i,    η = (z − z_bottom) / H
```

On the D2450 family `G ≈ 1.003609`, giving `U_MEAN = 0.199281` for a 0.2 m/s
target — a +0.3609% discrete-integration bias. That constant is **mesh-specific**.
Reusing it on a mesh with different inflation or z-discretization silently corrupts
LMH with no error at all. Cross-geometry LMH comparisons are the entire point of
the campaign, so this must be caught mechanically.

**`G` is the mesh invariant, not `U_MEAN`.** `U_MEAN` depends on the target
velocity and therefore differs across the 0.1 / 0.2 / 0.3 sweep on the same mesh.
Store `G` in the mesh manifest; store `u_target_ms` in the run manifest.

### Design — solver-side, lazy fill, no second Fluent start

`260815_RO_UDF.c` already computes `G` at runtime in `ensure_inlet_G`. Parse that
value rather than recomputing it mesh-side. This avoids a second Fluent product
start (~300 s), and more importantly the asserted value is the one actually used —
an independent mesh-side quadrature could diverge from the UDF's, creating a new
silent failure mode in place of the one being closed.

1. **New dated UDF.** Copy `260815_RO_UDF.c` to `260816_RO_UDF.c` and point
   `run_config.udf_source_file_name` at it. Do **not** edit `260815` in place —
   there is a frozen-UDF regression test, and it has already produced one false
   alarm via CRLF.
2. **Marker line.** `print_inlet_profile_stats` prints `G = %.6g`, which is only
   ~1e-6 relative and not uniquely greppable. Add a dedicated single-token line:
   ```
   RO_UDF_INLET_PROFILE_G=1.0036090123
   ```
   `%.12g` or `%.10f`, emitted with `Message0` so it is not repeated 50× under MPI.
   Print it from `ensure_inlet_G` after `G` is accepted — not from the `G = 1`
   fallback path, which must remain distinguishable as a failure.
3. **Timing.** The check must abort *before* `iterate`, so `G` has to be emitted by
   the execute-on-demand probe after `libudf` loads, not by the profile hook.
   That probe is currently gated on `run_inlet_profile_probe or
   use_inlet_velocity_profile`. The gate becomes always-on, otherwise a run with
   the probe off never emits the marker.
4. **Parse.** In `solver_code_260616.py`, after the probe TUI and
   `assert_transcript_contains(INLET_PROBE_MARKER, ...)`. Read from
   `list_solver_transcript_paths` / `fluent-*.trn` — **not** `solver_log_*.txt`.
   This is the same transcript-bounce rule already documented at the existing
   comment near that call site.
5. **Lazy fill.** If the mesh manifest's `inlet_profile_G` is null, write it once.
   Never overwrite. Every later run compares and aborts before iterating if the
   relative difference exceeds 1e-6.
6. **Missing marker is an error**, both before and after iterate. Do not silently
   skip the check.

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
| `max_iterations` | Code is consistently 2000. `DEPLOY_RUNBOOK.md:65` saying 1000 is stale documentation. `07`'s 3000 is a different knob (rerun budget). Fix the doc in step 10. |

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
6. **CLI still `--geo-name` / `--case-name` while disks use four ids.** Change the
   flags in the same commit as the builders (6c).
7. **`RO_DATA_ROOT` accidentally set to the repo on the dev machine.** Tests would
   write into git. Tests use `tmp_path` only and `monkeypatch.delenv` in the
   defaults case; they never read the developer's env.
8. **Inventory exiting 0 on an empty scan.** New inventory must raise
   `NotADirectoryError` if `runs_root()` is missing, not report "Total cases: 0".
   That exact silent success is what motivated this whole restructure.
9. **pytest green without `pip install -e .`, then the server fails at
   `import ro`.** `pythonpath=["src"]` covers pytest only. Runbook step 1 on the
   server is `pip install -e .`; the `__main__` guard makes the omission loud.

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

## 10. Step detail for the remaining work

**Step 3 — imports.** `from _x import ...` → `from ro.x import ...` across scripts,
configs, tests, and the internal imports inside `src/ro/` itself. `03b` currently
loads `_shear_cff_mu_guard` via `importlib` on a sibling path; that becomes a normal
import. Shims stay. Nothing moves. No path or manifest work.

**Step 4 — delete shims.** `conftest.py` keeps only `tests/` on `sys.path` for
`helpers`. `helpers.load_solver_common()` imports `ro.solver_common`. This is the
727/3 checkpoint after packaging.

**Step 5 — add `paths.py` and `manifest.py` plus tests.** Nothing in production
imports them yet. Tests: env precedence, cwd independence, importlib-by-path load,
`RO_DATA_ROOT` unset raises, relative raises, `min0006` raises, `_7c` geo_id
raises, builder round-trip, manifest round-trip on `tmp_path`, stale-path check,
overwrite guard.

**Step 6a — builders only.** Every path construction goes through `ro.paths`.
Manifests not yet read. Layout registry untouched. Delete every `parents[N]`
project-root locator in the same commit as the file it lives in.

**Step 6b — manifests.** Write on mesh and run creation. Inventory and post glob
manifests. `layout_from_mesh_manifest(mesh_directory) -> GeometryLayoutRecord`
already exists (step 9); remaining 6b work is to **call it** from inventory and
post instead of `resolve_layout`. Delete
`parse_case_operating_values` and the `write_report_config` identity. 6b-3
deleted the uncalled `final_case_data_paths` /
`final_case_data_paths_under_root` wrappers and `strip_mesh_suffix`; 06 and 07
already resolve cas/dat from the run directory locally.

Inventory currently walks two levels (`geo/case`) and then `os.walk`s logs
from whatever directory that walk picked. Under
`runs/{family}/{geo_id}/{mesh_id}/{run_id}` that directory is the geo_id, so
one inventory "case" can merge transcripts from every run under that geo.
Harmless at Total cases: 1; with 495 runs it would synthesize a single
`stop_reason` from several transcripts — a genuine silent failure.
Manifest-based identification (one record per run `manifest.json`) removes
this structurally. Do not keep a recursive log walk from a parent of multiple
runs.

**Execution-order amendment (2026-08-21).** Split 6b so writers populate metadata
before readers migrate:

1. **6b-write:** mesh and solver workers write manifests. No downstream reader
   consumes them yet.
2. **6a-2b:** `07_batch_solver_rerun.py` reads the four ids from each candidate
   run manifest and uses `run_dir()`. Missing manifests are fatal; there is no
   name-parsing or legacy-path fallback.
3. **6a-3:** migrate post-processing paths, including the previously omitted
   `01_pyfluent_report_extract.py` and `rebuild_mesh_ledger_from_logs.py`.
4. Continue the remaining 6b reader/inventory/layout work.

**Step 6c — CLI.** `--family --geo-id --mesh-id --run-id` replace `--geo-name` /
`--case-name`.

**Step 7 — reshuffle**, one commit per tree: 7a `01_Templates`→`templates/` and
`02_UDFs`→`udfs/` (update `test_udm_layout_parity.UDF_DIR`); 7b configs; 7c entry
points to `scripts/` keeping numeric prefixes so subprocess-string churn is
isolated; 7d docs/archive/analysis, remove the empty `My_CFD_Project/`, drop
`03_Results` and `00_Geometries` from `.gitignore`.

**Step 8 — prefixes.** `git mv scripts/07_batch_solver_rerun.py
scripts/batch_solver_rerun.py` etc. Update `06`'s subprocess constants, `07`'s
`--report-script` default, and `AGENTS.md`'s filename freeze in the same commit so
the freeze matches the tree.

**Step 9 — registry removal.** `layout_from_mesh_manifest` only.
`resolve_layout(geo, mesh)` raises `RuntimeError("layout comes from the mesh
manifest")` — loud, never a silent fallback to 1+3+1. Delete the dirname parsers;
keep the solver-log parser as a diagnostic that compares against the manifest
and raises on mismatch. Also delete the empty
`scripts/04_pyensight_streamline_export.py`.

**Step 10 — docs.** `AGENTS.md` (amend the layout, tree, entry-point-name, and
`00_Geometries` freezes), `DEPLOY_RUNBOOK.md` (`/c/pyfluent`,
`RO_DATA_ROOT=C:\ro_data`, `pip install -e .`, the stale `max_iterations` line).

---

## 11. Verification gate

Before starting the 495-run campaign, re-run one case (`D2450_a45`) end to end on
the new tree and confirm LMH, evaluation-window ΔP, and CP are consistent with the
pre-restructure figures. The legacy `03_Results` tree is frozen under
`RO_DATA_ROOT/archive/` and the `_scratch` logs are archived at
`archive/logs/scratch_20260820.tgz` if a number needs to be recovered for
comparison.
