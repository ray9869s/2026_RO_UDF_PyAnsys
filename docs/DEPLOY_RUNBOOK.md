# Server deploy runbook

Paste these commands into **Git Bash on the Windows server**. Fluent / PyEnSight
run only there, never in WSL.

Current branch: `main`.
Repo root: `/c/pyfluent` (`C:\pyfluent`).
Data root: `C:\ro_data` (`RO_DATA_ROOT`).

---

## 0. Session setup (every Git Bash window)

```bash
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
```

`RO_DATA_ROOT` must be absolute. Unset or relative raises. It never defaults to
the git tree. `pip install -e .` is required for every worker (`import ro`);
pytest's `pythonpath = ["src"]` does not cover CLI scripts.

---

## 1. Pull and install

**Duration:** seconds

```bash
cd /c/pyfluent
git switch main
git pull --ff-only origin main
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

`requirements.txt` is the Ansys/jupyter pin list. `requirements-dev.txt` is
`-r requirements.txt` plus pytest. Do not `pip install pytest` alone.

---

## 2. Sanity check — test suite once

**Duration:** under 1 minute

```bash
cd /c/pyfluent
source .venv/Scripts/activate
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
```

On WSL the same selection is `scripts/run_full_pytest.sh` (uses `.venv/bin/python`).
WSL measured 2026-09-11: **1059 passed, 1 skipped, 0 failed**. The skip is
`tests/test_campaign_geo_ids.py` when `RO_DATA_ROOT` is unset. Re-measure on
this Windows host; do not copy the WSL count. Any failed test means the pull
or the venv is wrong — do not proceed.

---

## 3. Layout

### Code (`PYFLUENT_PROJECT_ROOT`, `/c/pyfluent`)

```
configs/     run_config.py, batch_config.py, post_config.py, batch_post_config.py
scripts/     workers and orchestrators (no numeric prefixes)
templates/   Fluent case/CFF templates
udfs/        dated *_RO_UDF.c — filenames unchanged
src/ro/      installable package
```

Production UDF is `udfs/260822_RO_UDF.c` (`run_config.udf_source_file_name`).

### Data (`RO_DATA_ROOT`, `C:\ro_data`)

```
geometries/{family}/{geo_id}/{geo_id}.dsco
meshes/{family}/{geo_id}/{mesh_id}/{geo_id}_{mesh_id}.msh.h5
runs/{family}/{geo_id}/{mesh_id}/{run_id}/{geo_id}_{run_id}_final.cas.h5
inventory/   case_inventory.csv, rerun_candidates.csv, mesh_ledger.csv
archive/     frozen legacy 03_Results; no code reads it
```

Canonical diamond checkpoint (physics-neutral remesh, 796,009 cells):

```
meshes/diamond/D2450_a45/max085_min006_cpg5_bl4_peel2/D2450_a45_max085_min006_cpg5_bl4_peel2.msh.h5
runs/diamond/D2450_a45/max085_min006_cpg5_bl4_peel2/u0p2_p6M/D2450_a45_u0p2_p6M_final.cas.h5
```

`batch_config.py` `common_solver_settings.max_iterations` is **2000**.
`batch_solver_rerun.py`'s 3000 is a different knob (rerun budget).

`mesh_batch_cases` is the isolated `D0817_a30` `max085_min006_cpg7_bl4_peel2` mesh. It is the default `--case-set` for `batch_meshing.py`. Dry-run with `python scripts/batch_meshing.py --dry-run` (do not flip `batch_config.dry_run`). The 31-mesh campaign lives in `production_mesh_batch_cases` and is reached only with `--case-set production`. The parked 22-case ML/Pillar/Sin/empty list is `_MESH_BATCH_CASES_EXPLORATORY`.

---

## 4. Inventory

Default scan root is `RO_DATA_ROOT/runs`. Default output is
`RO_DATA_ROOT/inventory`. Orchestrator selectors are
`--family --geo-id --mesh-id --run-id`.

```bash
cd /c/pyfluent
python scripts/case_inventory.py
```

`--max-iter` defaults to `batch_config` `max_iterations` (**2000**). Pass
`--max-iter` only to override.

Inventory identifies cases from `manifest.json`, not a two-level directory walk.
A missing `runs/` tree is an error, not `Total cases: 0`. Cas/dat names must be
`{geo_id}_{run_id}_final.{cas,dat}.h5`; a unique leftover `*_final` file is a
loud mismatch, not a silent success.

Writes `case_inventory.csv`, `case_inventory_compact.csv`,
`case_inventory.json`, `case_inventory_summary.txt`, and
`rerun_candidates.csv`. Compact rows carry `family`, `geo_id`, `mesh_id`,
`run_id`, and `case_dir`. CSV columns `geo_name` / `case_name` are aliases of
`geo_id` / `run_id`.

`batch_solver_rerun.py` reads `inventory/active_solver_rerun_candidates.csv`.
That file is **manually promoted** from `rerun_candidates.csv`. Nothing in the
repo writes `active_*`. A missing file is an error; a header-only file is an
empty queue.

---

## 5. Meshing and solving

Workers take `PYFLUENT_RUN_OVERRIDES` JSON. Four ids locate the leaf;
`geo_name` / `case_name` are filename labels and must equal `geo_id` / `run_id`
(solver) or `geo_id` / `mesh_id` (meshing).

The nine-point production sweep (`u` = 0.1/0.2/0.3 × `p` = 4/6/8 MPa, 31 × 9 = 279) is

`python scripts/batch_solver_sweep.py --case-set production`

The p6M column only (31 geos × 3 velocities = 93) is

`python scripts/batch_solver_sweep.py --case-set production --outlet-gauge-pressure 6.0e6 --dry-run`

`--dry-run` prints `Selected cases (Fluent has not launched):` and per-case skip decisions. Drop `--dry-run` only after that list is 93 and `_p6M`. A pressure that is not in the case-set is an error, not an empty sweep. Do not flip `batch_config.dry_run`; that flag is shared with meshing.

The matching extract for that p6M column is

`python scripts/batch_report_extract.py --case-set production --outlet-gauge-pressure 6.0e6 --dry-run`

Drop `--dry-run` after the list is 93 `_p6M` four-ids. No `--case-set` still walks every run manifest (grid-study / `_conv2000` / p8M included). Matrix-selected leaves with no final cas/dat are `SKIPPED_MISSING_FINALS` (not a batch failure). Leave `post_cases` empty.

Default `python scripts/batch_solver_sweep.py` stays on exploratory `solver_sweep_cases` (currently the `D0817_a30` `max085_min006_cpg7_bl4_peel2` `u0p3_p6M` mesh-sensitivity solve). Do not point the default entrypoint at 279. Parked GTS list is `_SOLVER_SWEEP_CASES_GTS_PILOT`. The previous seven-case list is `_SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS`.

Production mesh leaves look like

`meshes/diamond/D2450_a45/max085_min006_cpg5_bl4_peel2/D2450_a45_max085_min006_cpg5_bl4_peel2.msh.h5`

Every production geo uses `max085_min006_cpg5_bl4_peel2` except `D0817_a60`, which uses `max060_min006_cpg5_bl4_peel2`. Solver output is

`runs/.../{run_id}/{geo_id}_{run_id}_final.cas.h5`.

Do not remesh a hashed `.msh.h5` that still has dependent run manifests.
`--force` on `meshing_code_260616.py` is refused while any run cites that
`mesh_sha256`.

---

## 6. Post-processing

```bash
python scripts/batch_postprocess_all_cases.py \
  --family diamond \
  --geo-id D2450_a45 \
  --mesh-id max085_min006_cpg5_bl4_peel2 \
  --run-id u0p2_p6M \
  --run-reports \
  --dry-run
```

A complete four-id selection resolves through `run_dir()` and refuses a missing
manifest. Layout comes from the mesh manifest, not a name-keyed registry.
`--geo-name` / `--case-name` are not orchestrator selectors.

Results and logs go under `RO_DATA_ROOT/inventory/`, not `03_Results/_inventory/`.

---

## Historical — July 2026 `03_Results` playbook (do not follow)

The F-01 / F-02 / F-20 procedure that used to occupy this file targeted the
Sin_ST / Sin_SL deadline campaign under `My_CFD_Project/03_Results`, with
`--geo-name` / `--case-name`, `max_iterations` 1000, and
`u0p*_p*M__mesh_max100_min006_cpg3_bl3` filenames.

That tree is frozen under `RO_DATA_ROOT/archive/`. No current worker reads it.
Do not `git pull origin main`, do not expect 86 tests, do not run
`My_CFD_Project/01_Scripts/post_processing/00_…` or `06_…`, and do not grep
`03_Results/_inventory`. Recover a number from the archive only if a paper
comparison requires it.
