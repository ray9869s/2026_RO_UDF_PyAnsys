# AGENTS.md

Handoff for the next agent (Claude Code, Codex, Cursor). This file is the
live campaign. The longer project guide — UDF immutability, path builders,
cross-platform tests, metrics — is `docs/AGENTS.md`. Read that before
changing shared helpers. This checkout cannot see the Windows data disks.
Do not invent transcript lines, hashes, or leaf contents.

Code is edited in WSL. Ansys Fluent 25.1 runs only on the Windows host
(Git Bash, repo `C:/pyfluent`, prompt host `DESKTOP-3EO9RAJ`). Sync is
`git push` from here and `git pull` there, and only when the host queue
is idle. One Fluent session at a time (`processor_count = 50`). A second
session fights the license and loads another multi-million-cell case.

## Repo layout

```
src/ro/                 paths, manifests, solver helpers, monitors
src/ro_2d_pilot/        2D pilot. It does not change the 3D campaign.
scripts/solver_code_260616.py
scripts/meshing_code_260616.py
scripts/batch_solver_sweep.py
scripts/batch_report_extract.py
scripts/campaign_v3.py
scripts/convergence_audit.py
scripts/mfbo/           mesh_study_case, job_queue, reextract, locate
configs/run_config.py
configs/mesh_spec_v3.json
udfs/260822_RO_UDF.c    production UDF. Dated files stay byte-identical.
tests/
docs/AGENTS.md
```

CLI scripts need the host or WSL venv (`pip install -e .`). Pytest uses
`pythonpath = ["src"]` and does not launch Fluent.

## Entry points

- `scripts/mfbo/mesh_study_case.py` — mesh, solve, and extract one case.
  Campaign jobs share `_study_argv` in `scripts/campaign_v3.py`: pmdb
  geometry, mesh spec, `--reuse-mesh`, `--checkpoint-every 200`,
  `--convergence-profile campaign_v3`. `--mesh-only` does not solve.
  `--continue-iterations` / `--continue-every` extend a finished final.
  `--max-iterations` overrides the cap. `--no-cp-live` skips the live CP
  report and leaves the stop on `cp_window`. `--max-aspect-ratio 250`
  selects a hard global ceiling and a distinct `_ar250` mesh leaf.
- `scripts/campaign_v3.py` — 31 geometries, p6M, on
  `C:/ro_data_mfbo/campaign_v3`. Velocity order is 0.2, then 0.1, then
  0.3 m/s, followed by 0.05, 0.15, 0.25 m/s. The additional run cases
  are `u0p05_p6M`, `u0p15_p6M`, `u0p25_p6M`; every setting other than
  inlet velocity and run identity is inherited from `u0p2_p6M`.
  `--emit-queue` writes JSON; without `--run-id` it now selects all
  six speeds (186 jobs). Repeat `--geo-id` to restrict geometries.
  `--max-iterations FAMILY=N` is repeatable, defaults to 2000 for
  every family (`diamond`, `empty`, `ml`, `pillar`, `sin`), and applies
  to solves. `--no-cp-live` passes through to solves.
  `--max-aspect-ratio 250` selects sensitivity meshes for emission,
  mesh tables, comparisons, and seminar CSVs. `--compare` reads
  `campaign_reextract_v2`. It does not launch Fluent on import.
- `scripts/mfbo/job_queue.py` — one job at a time. Sidecars are
  `<queue>.state.json`, `<queue>.lock`, `<queue>.stop`,
  `<queue>.summary.log`, and `logs/<id>.log`. Exit 0 when nothing failed,
  2 when a job failed, 1 on preflight. A lock held by a live pid refuses
  another start.
- `scripts/mfbo/reextract_runs.py` — re-extract a leaf outside production.
  Renames `post/reports` to `post/reports_prev_<UTC>/`.
  `--copy-from-production` copies the leaf, the mesh, and the geometry.
  It never points Fluent at `C:/ro_data`.
- `scripts/convergence_audit.py` — PASS leaves only. Stop iteration,
  continuity, nacl, and the last-20 / last-100 relative change of LMH,
  spacer pressure drop, and CP. Report files are the source of record.
- `scripts/mfbo/locate_mass_imbalance.py` — read-only diagnosis. Copies
  the leaf under `--data-root` and bins mass imbalance. Writes
  `mass_imbalance.md`, `top_cells.csv`, `histogram.csv`, `summary.json`.

Workers stay dated: `meshing_code_260616.py`, `solver_code_260616.py`.
Paths go through `ro.paths`. A leaf is
`runs/<family>/<geo_id>/<mesh_id>/<run_id>/`.

## Data roots

| Root | What it holds | Writes |
| --- | --- | --- |
| `C:/ro_data` | Production geometries, meshes, runs, inventory. Campaign mesh `max085_min006_cpg5_bl4_peel2` (D0817_a60 uses `m_max` 0.060). | Never. Read, or copy out. |
| `C:/ro_data_mfbo/meshstudy` | Mesh studies (bulk size, y1, contact). | Study leaves only. |
| `C:/ro_data_mfbo/campaign_v3` | Benchmark v3. Mesh id `max035_min006_cpg5_bl4_peel2_y1h020_bms034`. Geometry from `C:/ro_data_mfbo/geometries_pmdb`. Spec `configs/mesh_spec_v3.json`. | This campaign. |
| `C:/ro_data_mfbo/campaign_reextract_v2` | Re-extracted production BL4 leaves. The v3 `--compare` baseline. | Re-extract copies only. |
| `C:/ro_data_mfbo/mfbo3d` | 3D HF/MFBO studies under `studies/`. | That study. |
| `C:/ro_data_mfbo/mass_imbalance` | Copies for `locate_mass_imbalance`. | The copy and its report. |

`geometries/` under a study root are copies. The production `.dsco` is
never moved. v3 meshing uses `--geometry-suffix .pmdb` and
`--geometry-root C:/ro_data_mfbo/geometries_pmdb`.

## Hard rules

- Never write to `C:/ro_data` or `/mnt/c/ro_data`. Drivers refuse that
  root. Do not relax the refusal so a copy will load.
- Never `git pull` on the host while a queue is running from
  `C:/pyfluent`. The running process is the tree it started with.
  Pull only after that queue exits. Do not ask the operator to pull
  while main4 is running.
- No `job_queue.py run --resume` and no `--retry-failed` unless the
  operator asks. `--resume` retries failed jobs and appends `--resume`
  onto a `mesh_study_case` solve. A fresh start is `job_queue.py run
  <queue.json>` with neither flag.
- Move a leaf aside instead of deleting it. Rename the directory to
  `<name>.prev_<UTC>`, UTC as `YYYYMMDDTHHMMSSZ`
  (`u0p2_p6M.prev_20261116T130000Z`). The same stamp is how
  `reextract_runs` keeps `post/reports_prev_<UTC>/`. Do not delete
  `*_iter_*` snapshots or `post/reports` to “clean” a leaf.
- Tests must pass before a commit:
  `scripts/run_full_pytest.sh`
  (`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`).
  WSL and the Windows host are separate runs. A WSL pass is not a
  Windows baseline. Then commit and push with a message that says why.
  The host does not see the fix until it pulls, and it pulls only when
  idle.
- On a non-trivial code change, say what you will change and wait.
  Small commits, one concern each. Ambiguous leaves: stop and ask.
- Do not overlap Fluent sessions.

Host shell, before any smoke or queue:

```bash
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
```

A shell `RO_DATA_ROOT=C:/ro_data` is normal for production reads. Study
commands pass `--data-root` themselves. `locate_mass_imbalance` must
see `RO_DATA_ROOT` equal to the copy root (`C:/ro_data_mfbo/mass_imbalance`),
which the script sets around the copied manifest.

## Smoke first

Any change to solve, continue, or extract code needs a 20-iteration
host smoke before a long queue. Pytest does not start Fluent. The smoke
uses the real campaign_v3 mesh, UDF, and profile.

Move the real `u0p2_p6M` leaf aside first. `--case` is the base run id,
so the smoke writes that same leaf. Put the real leaf back when the
smoke is done. A cap of 20 sits inside the flux ramp (full at iteration
150, hold through 200): residual and QoI stopping stay off, and the
solve must not refuse to start. Expect residual rows 1–20,
`checkpoint_000010` and `checkpoint_000020`, and a non-zero window
flux in `post/reports`. Zero residual lines means iterate never ran.
Do not start the long queue on that code.

Fresh smoke, `cp_live` on (the default after `294496e`):

```text
python scripts/mfbo/mesh_study_case.py --data-root C:/ro_data_mfbo/campaign_v3 --geo-id REF_empty --case u0p2_p6M --m-max 0.035 --m-min 0.006 --m-cpg 5 --bl-layers 4 --membrane-bl-y1-um 2 --bulk-max-size 0.034 --peel-layers 2 --max-prism-aspect-ratio 250 --geometry-suffix .pmdb --geometry-root C:/ro_data_mfbo/geometries_pmdb --mesh-spec configs/mesh_spec_v3.json --reuse-mesh --checkpoint-every 10 --max-iterations 20 --convergence-profile campaign_v3
```

Control, already passed on `ae98e7c`: the same command with
`--no-cp-live`. Continue smoke, only on a final that is the real
2000-iteration solution (D2450_a45 `u0p2_p6M`). Expect new residual
rows through iteration 2020 and checkpoints `00002010` and `00002020`.

```text
python scripts/mfbo/mesh_study_case.py --data-root C:/ro_data_mfbo/campaign_v3 --geo-id D2450_a45 --case u0p2_p6M --m-max 0.035 --m-min 0.006 --m-cpg 5 --bl-layers 4 --membrane-bl-y1-um 2 --bulk-max-size 0.034 --peel-layers 2 --max-prism-aspect-ratio 250 --geometry-suffix .pmdb --geometry-root C:/ro_data_mfbo/geometries_pmdb --mesh-spec configs/mesh_spec_v3.json --reuse-mesh --checkpoint-every 200 --convergence-profile campaign_v3 --continue-iterations 20 --continue-every 10
```

## Known pitfalls

- Fluent expressions. A bare `udm-7` is the subtraction `udm - 7`.
  Fluent then prints `Setup contains invalid expression` and `iterate`
  returns with no residual line. Memory fields in an expression are
  `{udm-6}`, `{udm-7}`, `{udm-9}`. The live mean is the named expression
  `cp_live_mean`. The `cp_live` report is that name divided by the
  window bulk, same shape as `cp_window`. The area floor is
  `1e-30*Area(clip)`, not a `[m^2]` literal. Before `iterate`, the
  solver prints the Expression / Error / Used In table and raises
  `Refusing to iterate` when the setup is invalid.
- `check_convergence` on a converged case. A finished leaf is already
  under the residual target. Leaving the check on makes `iterate`
  return immediately (`2000 -> 2000`). A continue keeps the residual
  check off and advances with `iterate(iter_count=step)` where `step`
  is the additional count. Fluent 25.1 `run_calculation` has no
  `number_of_iterations` attribute. Do not assign it, and do not write
  the settings child `iter_count` to the chunk step.
- CP949. The Korean Windows console emits bytes that are not UTF-8.
  A strict text pipe raises `UnicodeDecodeError` inside
  `_readerthread` and aborts extract. Decode with
  `ro.child_output` (`errors="replace"`). The host shell sets
  `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`.
- `-mpi=intel`. This host has 112 logical CPUs. Fluent’s Microsoft MPI
  default aborts with `Failed to construct hwtree` (`0x8000ffff`).
  Every launch passes `-mpi=intel` (`configs/run_config.py`
  `fluent_mpi`, overridable with `RO_FLUENT_MPI`). An empty value omits
  the flag and brings the abort back.
- Hang watchdog. Solve idle 60 min, extract idle 30 min
  (`solve_hang_idle_minutes`, `extract_hang_idle_minutes`). A quiet
  attempt log, transcript, and `post/` tree kills the recorded PIDs,
  prints `HANG failure_class=hang`, and exits 75. Extract is retried
  once. Solve is not. That failure is not a reason to `--resume`.
- `cp_live` vs `--no-cp-live`. Default is `cp_live` on, and the v3 stop
  watches it. `--no-cp-live` keeps the `ffd53e1` stop on `cp_window`.
  main3 was started with `--no-cp-live` because the unbraced expression
  was rejected. Do not flip that flag on a running queue. The next
  solve uses `cp_live` only after the 20-iteration smoke above passes.
- Mesh id suffixes. `_fsNNN` is the membrane face size in millimetres
  (`0.025` → `_fs025`), inserted after `_peel`. Growth rate 1.2 is
  omitted; any other rate appends `gNNN` (`_fs020g115`). The campaign
  id with no face-size override is
  `max035_min006_cpg5_bl4_peel2_y1h020_bms034`. Adding `_fsNNN` is a
  new mesh leaf. `configs/mesh_spec_v3.json` `geometries` is `{}`
  until a host mesh test picks a size. A geometry entry
  `{"membrane_face_size_mm": ...}` becomes `local_face_size` on
  `wall_top_mem` and `wall_bottom_mem`.
- Hard global AR sensitivity. `--max-aspect-ratio 250` on either
  campaign driver appends `_ar250` after all sizing tokens:
  `max035_min006_cpg5_bl4_peel2_y1h020_bms034_ar250`. It cannot alias
  the standard mesh or an `_fsNNN` mesh. Combined finer-face / AR
  requests keep both tokens and are a fourth, separate mesh identity.
  This option replaces the prism-only override with the hard global
  check; it cannot combine with `--max-prism-aspect-ratio`. Quality
  parsing is required. Orthogonal quality, skewness, and skewed-face
  fraction keep their hard limits. The mesh manifest records
  `aspect_ratio_gate: "hard"`, `max_aspect_ratio_threshold: 250.0`,
  and measured `AR_max`. A missing, inconsistent, or exceeded ceiling
  refuses manifest acceptance or reuse. An AR above 250 still fails
  before the worker writes the final mesh.

## Current state (2026-11-18)

**main4 is running from `C:/pyfluent`**, as reported by the operator.
Its starting revision, flags, jobs, and results were not supplied.
Push these additions to GitHub only. The operator will pull on the
host after main4 exits. Do not modify the host checkout,
change a running queue, or overlap Fluent sessions.

The previous main3 handoff below is historical evidence, not a claim
about main4 or proof that its pending host checks have passed.

WSL validation for these additions: `scripts/run_full_pytest.sh`,
2346 passed, 1 skipped, 4 existing curvature warnings. CLI-only checks
emitted 93 new-velocity jobs and 2 AR-sensitivity jobs under `/tmp`;
those WSL queues are validation artifacts, not host queues.

**Pending, after main4 exits and the operator pulls:**

1. Run the full Windows pytest suite separately. WSL/Actions results
   do not establish a Windows baseline. The solve-template and mesh
   gate additions need host validation; retain the 20-iteration smoke
   requirement above before any long solve queue. No host smoke was
   run from WSL. Reconcile the older pending checks below with main4's
   actual logs before rerunning any work.
2. New velocities. These commands only emit JSON and never launch
   Fluent. Run them from the prepared host shell above so queue `cwd`
   points at `C:/pyfluent`. All 31 geometries at the three new speeds
   (93 jobs; every family defaults to 2000):

```text
python scripts/campaign_v3.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/velocities_extra.json --run-id u0p05_p6M --run-id u0p15_p6M --run-id u0p25_p6M --no-cp-live
```

   A filtered example (6 jobs), with explicit per-family overrides:

```text
python scripts/campaign_v3.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/velocities_extra_filtered.json --run-id u0p05_p6M --run-id u0p15_p6M --run-id u0p25_p6M --geo-id REF_empty --geo-id P_p100_h00 --max-iterations empty=2000 --max-iterations pillar=2000 --no-cp-live
```

   Repeat `--max-iterations FAMILY=N` for measured family-specific
   caps; omitted families remain at 2000. These defaults are caps,
   not evidence of convergence at a new velocity. Omitting
   `--no-cp-live` selects live CP and requires its host smoke to pass.
3. AR sensitivity: D1225_a30 and S_a193_l1733 at the standard v3
   sizing and `u0p2_p6M`. The operator reported AR about 212 and 228;
   the new manifest must record what the new mesh actually measures.
   Leave `configs/mesh_spec_v3.json` `geometries` as `{}` for this
   standard-sizing comparison. Do not substitute finer-face entries
   into this queue. Emit the two mesh-and-solve jobs:

```text
python scripts/campaign_v3.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/ar250_u0p2.json --run-id u0p2_p6M --geo-id D1225_a30 --geo-id S_a193_l1733 --max-aspect-ratio 250 --no-cp-live
```

   These jobs mesh if the `_ar250` leaf is absent, otherwise reuse
   only a matching successful mesh. They then solve/extract under
   `runs/<family>/<geo_id>/max035_min006_cpg5_bl4_peel2_y1h020_bms034_ar250/u0p2_p6M/`.
   Existing run leaves are still refused. For mesh inspection before
   solving, emit a separate two-job mesh-only queue:

```text
python scripts/campaign_v3.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/ar250_meshes.json --mesh-only --geo-id D1225_a30 --geo-id S_a193_l1733 --max-aspect-ratio 250
python scripts/campaign_v3.py --mesh-table --max-aspect-ratio 250
```

   The table is read after those mesh jobs have finished. Success
   requires a written mesh, manifest gate `hard`, recorded ceiling
   250, measured `AR_max <= 250`, and orthogonal quality at least
   0.05. Compare the same geometries' extracted QoIs at `u0p2_p6M`
   with their separately identified finer-membrane meshes. Do not
   claim an AR sensitivity result until both solves/extracts exist.

Queue execution remains one job at a time, only after host validation
and main4 exits: `python scripts/mfbo/job_queue.py run <queue.json>`.
Do not add `--resume` or `--retry-failed`. Emit only the desired queue;
the examples above are alternatives, not instructions to enqueue every
example together.

## Previous handoff (2026-11-16; historical)

At that handoff, origin `main` was `65d6dcd`. The host queue was started from `ae98e7c`
and must keep running that tree.

**main3 was running**, with `--no-cp-live`, one Fluent at a time:

- Fresh `u0p2_p6M` solves: `empty/REF_empty`, `pillar/P_p100_h00`.
  Their earlier finals were the initialized field (no residual lines,
  `stop_reason` `iteration_unknown`, Jw = 0). The leaves were moved
  aside before this start.
- Thirteen continues, `--continue-iterations 2000 --continue-every 500`,
  same mesh id: `D0817_a30`, `D0817_a45`, `D0817_a60`, `D1225_a45`,
  `D1225_a60`, `D2450_a45`, `D2450_a60`, `M_c160`, `S_a072_l3465`,
  `S_a144_l3465`, `S_a144_l6930`, `S_a193_l3465`, `S_a193_l6930`.
  Families `diamond`, `ml`, `sin`. Pillars other than the `P_p100_h00`
  re-solve were already steady at 2000 and are not in this continue.

The 24 campaign PASS leaves that finished before `fafd064` are real
solves. Do not re-solve them because of the expression bug.

`294496e` braces the UDM fields, installs `cp_live_mean`, and refuses
to iterate when Fluent reports an invalid expression. It also drops
the `number_of_iterations` assignment. `65d6dcd` adds the per-geometry
face size and reads a mass-imbalance copy with
`RO_DATA_ROOT` set to `C:/ro_data_mfbo/mass_imbalance`. Neither commit
is in the running host tree. Pull them only after main3 exits.

**Previously pending after main3 (current completion is unverified):**

1. `cp_live` smoke. The `--no-cp-live` fresh smoke (REF_empty, 20/20)
   and the continue smoke (D2450_a45, iterations 2001–2020) already
   passed on `ae98e7c`. Still required: the fresh command in
   “Smoke first” with `cp_live` left on, after moving the finished
   `u0p2_p6M` leaf aside and putting it back afterwards. If Fluent
   rejects the expression, the solve stops before `iterate` and the
   table is in the transcript. Do not queue a `cp_live` solve on that
   result.
2. Plan B meshes. Four meshes report prism `status: no-prism` (one
   poly zone, class bulk), so the 250 prism ceiling never applies and
   they fail the bulk aspect-ratio limit of 150: `D1225_a30` (AR in
   185–228), `S_a072_l1733`, `S_a144_l1733`, `S_a193_l1733` (worst, AR
   228). Mesh-only the worst geometry at membrane face size 0.025 mm
   and 0.020 mm. Global `m_max` stays 0.035. Cell count, AR, and ortho
   are in the mesh log and manifest. Solve-time estimate is
   `cell_count / 4.34e6 * 6.5` hours. The operator picks the size that
   brings AR under 150. Until then, `geometries` in the spec stays
   `{}`. Then put that entry on all four geo ids.

```text
python scripts/mfbo/mesh_study_case.py --data-root C:/ro_data_mfbo/campaign_v3 --geo-id S_a193_l1733 --case u0p2_p6M --m-max 0.035 --m-min 0.006 --m-cpg 5 --bl-layers 4 --membrane-bl-y1-um 2 --bulk-max-size 0.034 --peel-layers 2 --max-prism-aspect-ratio 250 --geometry-suffix .pmdb --geometry-root C:/ro_data_mfbo/geometries_pmdb --mesh-spec configs/mesh_spec_v3.json --local-face-size "{\"labels\":[\"wall_top_mem\",\"wall_bottom_mem\"],\"size_mm\":0.025,\"growth_rate\":1.2}" --mesh-only
```

   Repeat with `"size_mm":0.020`. Mesh ids:
   `max035_min006_cpg5_bl4_peel2_fs025_y1h020_bms034` and
   `..._fs020_y1h020_bms034`.

3. D2450_a30 locate. The v3 leaf failed mass balance (continuity about
   1.3e-2, LMH and dP monitors flat) while the production mesh passed.
   The stale-path refusal is fixed in `65d6dcd` and has not been
   re-run on the host. This reads a copy. It does not write the
   campaign tree.

```text
python scripts/mfbo/locate_mass_imbalance.py --source C:/ro_data_mfbo/campaign_v3/runs/diamond/D2450_a30/max035_min006_cpg5_bl4_peel2_y1h020_bms034/u0p2_p6M --data-root C:/ro_data_mfbo/mass_imbalance
```

4. v3 at 0.1 and 0.3 m/s, with a per-family `max_iterations`. Wait for
   the thirteen continue extracts. Pillars stay at 2000 for both
   speeds. Diamond, sinusoidal, and `M_c160` wait until a continue
   snapshot shows LMH, spacer pressure drop, and live CP each changing
   by less than 1e-4 over 100 iterations. If a cap has to be chosen
   before that trajectory exists: `M_c160` was only 1.6e-4 at 2000, so
   3000 is enough to see the crossing; diamond and sinusoidal use 4000
   at 0.1 m/s and 3000 at 0.3 m/s. Replace those numbers with the
   iteration the continue extracts actually settle. 0.1 m/s is the
   slower feed, so the same drift takes more iterations. Do not invent
   a linear scale from 0.2 m/s. Do not emit that queue until the
   operator asks.
