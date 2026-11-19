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
  another start. Every runner also holds the host lock
  (`ro.host_lock`, `RO_HOST_LOCK`, default `~/.pyfluent/fluent_host.lock`),
  so a second queue file refuses to start, and before each job a Fluent
  process still alive after a 120 s grace (`RO_ORPHAN_GRACE_S`) refuses
  the start (exit 1, job stays pending). `succeeded` means exit 0 only;
  the state record and summary line (`gate=`) carry the leaf's
  `convergence_quality` from `RO_JOB_RESULT`. A job may set `timeout_s`;
  `run --job-timeout-hours H` sets the default. A timed-out job's process
  tree is killed and the job fails with `JOB_TIMEOUT` in its log.
- `scripts/mfbo/reextract_runs.py` — re-extract a leaf outside production.
  Renames `post/reports` to `post/reports_prev_<UTC>/` and copies the run
  `manifest.json` into that archive.
  `--copy-from-production` copies the leaf, the mesh, and the geometry.
  It never points Fluent at `C:/ro_data`.
- `scripts/convergence_audit.py` — PASS leaves only. Stop iteration,
  continuity, nacl, and the last-20 / last-100 relative change of LMH,
  spacer pressure drop, and CP. Report files are the source of record.
- `scripts/mfbo/locate_mass_imbalance.py` — read-only diagnosis. Copies
  the leaf under `--data-root` and bins mass imbalance. Writes
  `mass_imbalance.md`, `top_cells.csv`, `histogram.csv`, `summary.json`.
- `scripts/host/pending_checks.sh` / `.cmd` — Windows-only sequential
  pending checks, individual logs and append-only PASS/FAIL summary.
  They never pull or launch long solves. `pending_checks.py --plan` is
  read-only and can be run in WSL.
- `scripts/campaign_v3_reextract.py --emit-queue <json>` — observes the
  campaign root and emits one full re-extract job for each `u0p2_p6M`
  final that is PASS or whose `post/reports` is stale (sidecar hash ≠
  manifest final hash: every continued leaf, and D2450_a60 with no gate).
  It prints `STALE`/`SKIP` lines and skips a leaf whose final pair or
  manifest hashes are missing. Each job hashes the final pair against the
  manifest before archiving `post/reports`; existing queues are refused.
  `--emit-queue <json> --snapshot S_a072_l3465:2000 --snapshot
  S_a072_l3465:2500` emits snapshot jobs instead: each extracts the saved
  `*_iter_<N>` pair into `post/reports_iter<N>_meanfirst/` and never
  touches `post/reports` or the manifest gate.

Workers stay dated: `meshing_code_260616.py`, `solver_code_260616.py`.
Paths go through `ro.paths`. A leaf is
`runs/<family>/<geo_id>/<mesh_id>/<run_id>/`.

Reporting binds a row to its final: `campaign_v3` `leaf_metrics`,
`--compare` and `seminar_cases.csv` use `post/reports` only when its
`extract_source.json` hashes equal the manifest final hashes
(`ro.report_binding`); otherwise the metrics are blank and
`report_binding` says why. Exception, v2 baseline only: in `--compare`
a v2 leaf whose binding fails only for missing hash evidence (manifest
without final hashes, no sidecar, or a sidecar without the hash fields)
is reported with `v2_report_binding=unverified`, and `--compare` prints
the bound/unverified/blank counts. A v2 sidecar naming a different
final, a `BROKEN` marker or missing reports stay blank. v3 and
`seminar_cases.csv` stay strict. Extracts write `post/<dir>.tmp_<id>`, clear
the target sidecar and the manifest `convergence_quality` at start, and
swap in only after every gate (the replaced directory is kept as
`<dir>_prev_<UTC>`). A failed extract leaves its staging directory for
inspection and no PASS.

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

- Never write to `C:/ro_data` or `/mnt/c/ro_data`. Drivers and writers
  refuse that root through `ro.paths.refuse_production_root` (realpath,
  `..`, trailing dots, `\\?\` prefixes, junctions/samefile). Do not relax
  the refusal so a copy will load.
- Never `git pull` on the host while a queue is running from
  `C:/pyfluent`. The running process is the tree it started with.
  Pull only after that queue exits. Do not ask the operator to pull
  while main4 is running.
- No `job_queue.py run --resume` and no `--retry-failed` unless the
  operator asks. `--resume` retries failed jobs and appends `--resume`
  onto a `mesh_study_case` solve. A leaf whose final pair matches the
  manifest hashes then runs the extract only; a mismatch refuses. A fresh start is `job_queue.py run
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
- Hang watchdog. Solve idle 60 min, extract idle 30 min, mesh idle
  90 min (`*_hang_idle_minutes`), plus per-attempt wall caps of 48 h,
  2 h and 12 h (`*_wall_cap_hours`) that fire even while output flows
  (`HANG_REASON wall_cap`). A quiet attempt log, transcript, and `post/`
  tree kills the recorded PIDs, prints `HANG failure_class=hang`, and
  exits 75. stdout is read on its own thread, so a Fluent/MPI grandchild
  holding the pipe no longer blocks the stage. Extract is retried once.
  Solve is not. Campaign meshing writes `mesh_attempt<N>.log`. A hang is
  not a reason to `--resume`; after an extract hang `--resume` would only
  re-extract.
- Fail closed. `unknown_early_stop` (iterate returned below the cap with
  no stop marker) writes only `failed_attempt_*` and exits non-zero. The
  setup-expression check must pass positively: every named expression
  lists `Valid : Yes`, or, with none, at least one check form ran without
  an error; otherwise `Refusing to iterate`. If the host smoke refuses
  with `no expression check could run`, read the printed check forms
  before changing anything.
- Monitor-change columns. `monitor_change_last100_*` is now the
  end-to-end relative change (phase-sensitive on an oscillating monitor)
  and `monitor_trend_last100_*` the fitted slope over 100 iterations (the
  drift measure). CP reads `cp_live` only, so `--no-cp-live` leaves (all
  of main3) have blank new CP columns. LMH/dP use the active-span
  monitors. The old maximum deviation is `*_legacy`. Recorded only; the
  2000-iteration cap and the gate are unchanged.
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

**Audit fixes pushed after `8ca0706` (not on the host until main4
exits and the operator pulls).** Atomic extract and report/final
binding, stale-aware re-extract emitter with `--snapshot`, move-aside
resume fallback and BROKEN marker on a failed smoke restore, canonical
production guard, host-wide Fluent lock with orphan check, fail-closed
`unknown_early_stop` and expression check, job-state gate and
extract-only `--resume`, watchdog wall caps / pipe handling / queue
timeouts / meshing guard, end-to-end monitor change, window dP/L
columns. These change solve, extract, meshing and queue code. Before any
long queue, on the host:

1. Windows pytest.
2. The cp_live-ON fresh smoke in “Smoke first”, **and** the
   `--no-cp-live` control again: the earlier pass on `ae98e7c` does not
   cover the new fail-closed expression check. No Fluent 25.1 output of
   the check forms or of `list-properties` (`Valid : Yes`) is captured
   in this repo; the format comes from test fixtures. If either smoke
   refuses with `did not positively pass` or `no expression check could
   run`, save the printed check output and stop; do not relax the check
   blind. `pending_checks` runs only the cp_live-ON smoke.
3. One mesh-only smoke on a `.pmdb` geometry into a fresh study leaf
   (e.g. under `C:/ro_data_mfbo/meshstudy`) before the pending mesh-only
   steps: campaign meshing now runs with a piped stdout under the
   watchdog (`mesh_attempt1.log`), which no smoke with `--reuse-mesh`
   exercises.
4. The v2 binding check: count `campaign_reextract_v2` `u0p2_p6M`
   manifests with `final_data_sha256` and sidecars that match it. v2
   rows without that evidence are reported as `unverified` (the
   `--compare` summary line counts them); quote the count with any v2
   comparison. Host
evidence behind them (operator-reported): every continued leaf's
`post/reports` is from iteration 2000 while the final is 2500/4020;
D2450_a60 has no gate (main3 stopped mid-continue); `.out` files are
clean but oscillate; `c_b` sits 0.01–0.2 % below the x-normal cups;
Pillar/ML/Empty have no dead zone; v2 is not re-extracted and compares
via `cpc_window_avg_area`. Expect the re-extract emitter to list the
continued leaves as `STALE`. Run the S_a072 snapshot jobs to measure the
mean-first change over 2000→2500.

**Benchmark CP decision (approved 2026-11-18).** Use
`cp_meanfirst_live_window_avg` for campaign reporting, v3 comparisons,
and CP excess. Area-average cm and local cp over the same live
evaluation-window membrane faces, then divide once:
`(<cm>_live - <cp>_live) / (c_b_window - <cp>_live)`, where
`cp_f = B*cm_f/(Jw_f+B)` and live means `cp_f < min(c0, c_b_window)`.
Keep the existing `c_b_window_mol_m3` aggregation. Save `cp_ref_area_live`,
the live area and mask metadata. `cp_meanfirst_window_avg` includes all
window membrane faces and equals `cpc_window_avg_area`. Preserve every
existing CP definition/column; do not overwrite old per-face live canon.
The MFBO adapter and solver stop rule stay unchanged. Old summaries
without the new benchmark column remain blank in v3 comparisons.

Operator-reported evidence on S_a072_l3465, iterations 2000 -> 2500:
per-face live canon -1.37%, `cm_area_mean_window_live` -0.035%, LMH
+0.044%, dP 0.000%. This is supplied evidence, not a host measurement
made by WSL. See `docs/PAPER_METHODS_DOSSIER.md` for the full decision.

**main4 is running from `C:/pyfluent`**, as reported by the operator.
Its starting revision, flags, jobs, and results were not supplied.
Push these additions to GitHub only. The operator will pull on the
host after main4 exits. Do not modify the host checkout,
change a running queue, or overlap Fluent sessions.

The previous main3 handoff below is historical evidence, not a claim
about main4 or proof that its pending host checks have passed.

WSL validation for these additions: `scripts/run_full_pytest.sh`,
2364 passed, 1 skipped, 4 existing curvature warnings (2440 after the
audit fixes below). The host launcher's
read-only plan and Bash syntax were checked in WSL. Earlier CLI-only
checks emitted 93 new-velocity jobs and 2 AR-sensitivity jobs under `/tmp`;
those WSL queues are validation artifacts, not host queues. No Windows,
Fluent, or Task Scheduler success is claimed before the host runs them.

**Pending, after main4 exits and the operator pulls:**

Run `bash scripts/host/pending_checks.sh` from the prepared host checkout,
or launch `C:\pyfluent\scripts\host\pending_checks.cmd` as the interactive
Task Scheduler action (`schtasks /Create ... /IT`; start an already
configured task with `schtasks /Run /TN <task-name>`). The wrapper uses
`%ProgramFiles%\Git\bin\bash.exe`; override `GIT_BASH_EXE` if installed
elsewhere. Preview without launching Fluent:
`python scripts/host/pending_checks.py --plan`.

The launcher runs these checks in order, rechecking live Windows
processes and queue locks before each one:

- Fresh REF_empty `u0p2_p6M` cp_live-ON smoke: exactly residual
  iterations 1–20, nonempty checkpoint pairs 10/20, positive window
  LMH and a cp_live history. It requires a finished original beyond
  20 iterations, renames it to `.prev_<UTC>` before launch, keeps
  checkpoints, preserves the smoke as `.smoke.prev_<UTC>`, and restores
  the original in `finally`. A failed initial rename aborts the smoke
  before Fluent. A recovery JSON records both paths. After a forced
  process/machine termination, inspect that journal before another run.
- S_a193_l1733 mesh-only at 0.025 and 0.020 mm membrane face size,
  then D1225_a30 and S_a193_l1733 mesh-only with the hard global
  ceiling 250. Mesh leaves must be absent; existing leaves are refused
  and preserved. Each summary reports cells, max AR, minimum ortho and
  gate, including parsed mesh-log metrics on a gate failure. A finer
  candidate is offered only when its mesh passes and global AR <=150.
- D2450_a30 mass-imbalance locate on a fresh copy under
  `C:/ro_data_mfbo/mass_imbalance/pending_<UTC>`; prints cell/region bins.
- Timed full re-extract of the finished PASS P_p80_h15 `u0p2_p6M`
  final. Requires the new mean-first columns and mask, and records
  `extract_wall_time_s` from the fresh timing JSON plus elapsed time.
  Existing `post/reports` is archived; iteration reports are untouched.

Every step appends one PASS/FAIL line to
`C:/ro_data_mfbo/campaign_v3/queues/logs/pending_checks_summary.txt`.
Failures do not prevent later checks; an active queue/session prevents
any new Fluent launch. Per-step logs, recovery journal, and printed
follow-ups are under `queues/logs/pending_checks_<UTC>/`. Exit 0 means
all checks passed, 2 means an individual check failed, 1 is startup
refusal. A crash can require manual restoration from the recovery journal;
the script never deletes a leaf or kills unrelated sessions.

At the end it prints commands only. Choose one passing finer candidate
for the four failed geometries, then use its `CHOSEN_FACE_SIZE` for
the 0.10 m/s x31 queue. The helper adds explicit face-size overrides
without changing the canonical mesh spec/hash, so the test mesh can
be reused. AR250 solves remain standard sizing. Velocity follow-ups
are: bounded REF_empty 0.05 m/s smoke first (require PASS), 0.10 m/s
x31, then 0.05/0.15/0.25 m/s x five representatives: REF_empty,
D2450_a45, M_c267, P_p80_h15, S_a144_l3465. P_p100_h00 and M_c160 are
not the representatives. Printed solves use `--no-cp-live` unless
the initial cp_live smoke passed. No long solve or queue is started.

Re-extract queue emission after the checks (final reports only):

```text
python scripts/campaign_v3_reextract.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/reextract_pass_finals.json
python scripts/mfbo/job_queue.py run C:/ro_data_mfbo/campaign_v3/queues/reextract_pass_finals.json
```

The observed count is printed; do not hardcode 25 or invent missing
leaves. This uses the full extract. A targeted partial-extract path
is deferred until its bulk validation/report merge is validated.
Use the timed P_p80_h15 result for scheduling, not an invented runtime.

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
python scripts/campaign_v3.py --emit-queue C:/ro_data_mfbo/campaign_v3/queues/velocities_extra_filtered.json --run-id u0p05_p6M --run-id u0p15_p6M --run-id u0p25_p6M --geo-id REF_empty --geo-id P_p80_h15 --max-iterations empty=2000 --max-iterations pillar=2000 --no-cp-live
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
