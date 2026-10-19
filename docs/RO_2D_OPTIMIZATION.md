# 2D RO optimization pilot

Methodological pilot only. It is not the 3D campaign, and `very_fine` is
the target fidelity for this pilot, not a grid-independent solution.

The 3D production workflow (`src/ro/`, `scripts/solver_code_260616.py`,
`udfs/260822_RO_UDF.c`, `RO_DATA_ROOT`) is unchanged. This document records
the 2D closed loop. The 3D loop that calls the pillar evaluator is in
[3D loop](#3d-loop).

Code lives in `src/ro_2d_pilot/optimization/`. Case files and experiment
logs live under absolute `RO_2D_DATA_ROOT`, never in git and never under
`RO_DATA_ROOT`.

Offline replay checks the software and does not rank the methods. The live
comparison that was run is recorded below. It does not establish that MFBO
is generally cheaper than HF-only BO.

## Problem

2D channel, height `H = 0.770 mm`, circular obstacle, `n_pitches = 3`,
inlet `0.2 m/s`, pressure `6 MPa`.

| Quantity | Range |
|---|---|
| Obstacle diameter `d` | 0.30–0.50 mm |
| Streamwise pitch `L` | 3.0–5.0 mm |

The GP uses `x1, x2` in `[0, 1]`. Result records keep physical `d` and `L`.
Geometry still goes through `PilotConfig` (`d < H`, `L > d`).

Fidelities are mesh levels, not the campaign `low` / `high` labels:

| Role | Mesh level |
|---|---|
| Low-fidelity candidate | `medium` |
| Target / high fidelity | `very_fine` |

## Objective

Maximize LMH subject to

```
pressure_drop_per_length <= 30000 Pa/m
```

CP is a diagnostic and a final-report quantity. It is not an objective.

`30000 Pa/m` is a pilot constraint, not a membrane specification. The eight
valid `very_fine` screening points span about 13000–72200 Pa/m. This limit
keeps the solved center (`d = 0.40 mm`, `L = 4.0 mm`, about 25924 Pa/m)
feasible and leaves the short-pitch `d = 0.40 mm` point (about 33377 Pa/m)
and both valid `d = 0.50 mm` points infeasible, so the constraint is active
and the feasible set is nonempty.

## Fidelity screen used as qualification, not as the initial GP

A 3×3 medium / very_fine screen was collected for qualification. One
`very_fine` case diverged and is excluded from the correlations:
`d = 0.50 mm`, `L = 4.0 mm` (stopped at ramp 0.2, iteration 9).

Across the eight valid pairs, relative discrepancy is
`(medium − very_fine) / very_fine`. On 2026-10-03 the Windows host read
`RO_2D_DATA_ROOT/studies/fidelity_pairs/medium_very_fine_summary.json`
(`study = medium_very_fine_screening`, 9 geometries, 8 comparable pairs).
The rounded table matches that file. The same eight rows in
`src/ro_2d_pilot/optimization/replay.py` reproduce it. The summary's
`decision` is `not_made`: the screen does not accept medium as a
low-fidelity model.

| QoI | Pearson | Spearman | Mean signed | Mean absolute | Max absolute | Rank reversals |
|---|---:|---:|---:|---:|---:|---:|
| LMH | 0.985 | 0.929 | +2.14% | 2.14% | 2.62% | 2 / 28 |
| CP − 1 | 0.985 | 0.929 | −22.9% | 22.9% | 24.9% | 2 / 28 |
| dP/L | 1.000 | 1.000 | −0.31% | 0.68% | 2.05% | 0 / 28 |

LMH and CP−1 do not change sign across the eight pairs, so the signed mean
and the absolute mean are the same number. Medium LMH is always a little
high. Medium CP−1 is always about 23% low, which is why it is not a
quantitative substitute for `very_fine`. dP/L does change sign. The −0.31%
signed mean cancels a 0.68% absolute mean. The largest dP/L miss is 2.05%,
at `d = 0.50 mm`, `L = 5.0 mm`, where medium is low.

Both LMH reversals and both CP−1 reversals are the same two comparisons,
both at `L = 5.0 mm`: `(0.30 mm, 5.0 mm)` against `(0.50 mm, 5.0 mm)`, and
`(0.40 mm, 5.0 mm)` against `(0.50 mm, 5.0 mm)`. Medium ranks the
`d = 0.50 mm` point better on both quantities. `very_fine` ranks it worse.
The `very_fine` LMH gaps are 0.010 and 0.070 (24.7917 and 24.8509 against
24.7814), so these are near-ties, not a large reorder. dP/L has no
reversal.

The summary's solver-time ratio (medium / very_fine) is minimum 0.0787,
median 0.1059, maximum 0.1503. The median is about 9.4× solver speedup.
Median `very_fine` solver time on the replay rows is 758.1605 s, the same
denominator as the HF-equivalent cost below. Total wall-time ratio in the
same file is minimum 0.214, median 0.265, maximum 0.385, so medium startup
and extraction leave about a 2.6–4.7× wall-time speedup rather than the
9.4× solver speedup. Medium is informative enough to test a
multi-fidelity loop. That is not a claim that MFBO is better.

The one skipped geometry is `d0p50_L4p00`: medium `valid`, very_fine
`invalid`. That is the diverged `d = 0.50 mm`, `L = 4.0 mm` case above.

The full screen is a replay table and a reference. It is not loaded into
both optimizers at initialization.

## Common start and budget

Both methods start from the same four `very_fine` designs:

- `(0.40 mm, 4.0 mm)` center
- `(0.50 mm, 3.0 mm)`
- `(0.50 mm, 5.0 mm)`
- `(0.30 mm, 5.0 mm)`

`(0.30 mm, 3.0 mm)` is the best feasible screened HF point and is left out
so sequential search still has something to find.

MFBO also evaluates those four designs at `medium`. That cost is charged
and is not part of the common HF initialization.

Primary cost is solver time. Wall time is recorded and is not mixed into
the acquisition function.

```
HF-equivalent = cumulative solver seconds / 758.1605 s
```

`758.1605 s` is the median `very_fine` solver time of the eight valid
pairs. Normalized costs are `very_fine = 1` and `medium = 0.1059`. There
is no geometry-dependent cost model.

Pilot budget: 8.0 HF-equivalent, and at most 6 sequential queries. A final
`very_fine` evaluation of the recommended design is still required and is
charged. Cached valid results are not re-solved, but they still count
toward this benchmark cost.

Compare methods by the best HF-validated feasible LMH against cumulative
HF-equivalent cost, not by iteration count.

## Live pilot (`experiment_id = pilot`)

Both methods were run on the Windows Fluent host with `mode = live`,
`seed = 0`, and budget 8.0. Logs are
`RO_2D_DATA_ROOT/studies/optimization/{hf_bo,mfbo}/pilot/`. The comparison
script does not declare a winner.

| | HF-only BO | MFBO |
|---|---:|---:|
| Acquisition | constrained expected improvement | Kennedy–O'Hagan + Forrester |
| medium evaluations | 0 | 5 |
| very_fine evaluations | 8 | 7 |
| Sequential queries | 4 | 4 |
| Failures | 0 | 0 |
| Solver time | 5635.99 s | 5494.52 s |
| HF-equivalent cost | 7.434 | 7.247 |
| Summed case wall time | 7094.78 s | 7645.91 s |
| Final `d`, `L` | 0.375 mm, 3.0 mm | 0.375 mm, 3.0 mm |
| HF LMH | 25.3565 | 25.3565 |
| HF dP/L | 28481.7 Pa/m | 28481.7 Pa/m |
| HF CP | 1.07803 | 1.07803 |

Both histories name the same `very_fine` design. It satisfies
`28481.7 <= 30000` Pa/m, with about 1518 Pa/m of slack. `L` is the lower
edge of the pilot box. `d = 0.375 mm` is not one of the 3×3 screening
diameters. The best feasible screening point, `d = 0.30 mm`, `L = 3.0 mm`,
has HF LMH 25.3042, so this design is about 0.21% higher. An infeasible
neighbor, `d = 0.425 mm`, `L = 3.0 mm`, has a higher HF LMH (25.3906) and
dP/L 39599.8 Pa/m, so the reported point is the feasible incumbent, not the
unconstrained LMH maximum.

HF-only queried only `very_fine`. Its first sequential design was
`(0.375 mm, 3.0 mm)` and stayed the feasible incumbent. The next three
`very_fine` queries were `(0.400, 3.0)`, `(0.425, 3.0)`, and
`(0.425, 3.25)` mm. The last three are infeasible under the 30000 Pa/m
limit. Three of the eight HF-only rows were new Fluent runs. The four
initial rows and `(0.400 mm, 3.0 mm)` were valid results already on disk.

MFBO used the same four `very_fine` initials, then the same four designs at
`medium`, then four sequential queries: `very_fine (0.375, 3.0)`,
`medium (0.400, 3.0)`, `very_fine (0.300, 3.0)`, and
`very_fine (0.425, 3.0)` mm. Four of the five medium rows are that required
initial set. One medium row is a sequential choice. Every MFBO row was
reused from an existing valid result, including the two `very_fine` points
first solved while HF-only was running. This MFBO process launched no new
Fluent case.

Solver seconds and wall seconds in the table are the sum of the case
records in each history. A reused case is not solved again, and its stored
time is still added to the benchmark total. The wall column is therefore
not the clock time of the optimizer session, and it is not a cold-start
cost. On that accounting, MFBO spent 0.187 fewer HF-equivalent solver units
(about 2.5% of the HF-only total) and accumulated about 7.8% more case wall
time. The extra wall time is the medium cases' startup, not a measured
wait during the MFBO process.

Read this run as a closed loop on a small two-variable box: both methods
reached one HF-validated feasible design, and the solver-cost gap is small.
It is not evidence that MFBO is the cheaper method on a 3D spacer campaign.

On 2026-10-03, `scripts/compare_ro_2d_optimization.py` re-read the two
`pilot` directories on the Windows host and printed the same design and
the unrounded costs behind the table above: HF-only solver time
5635.9889471001225 s, HF-equivalent 7.433767582326068, wall time
7094.78346329974 s; MFBO solver time 5494.516356300213 s, HF-equivalent
7.247167791384824, wall time 7645.913412399823 s; both final HF rows
`d_m = 0.000375`, `L_m = 0.003`, LMH 25.356507233737542, dP/L
28481.694331910043 Pa/m, CP 1.078030037929223. The script reads
`final_summary.json` and exits if that file is missing, so this re-read
is the file on disk. A later Python literal `Path("/c/RO_2D_Data/...")`
reported the summaries missing: Git Bash rewrites `/c/...` only in
command arguments, not inside a script string. That probe did not show
an absent file. A later argument-path read of
`studies/fidelity_pairs/medium_very_fine_summary.json` succeeded and is
the fidelity section above.

## Models

NumPy only. BoTorch / PyTorch are not dependencies.

HF-only (`constrained_expected_improvement`): every new query is
`very_fine`. An RBF GP. Constrained expected improvement is LMH expected
improvement times the probability that dP/L is within the limit. A second
constraint multiplies a second probability. The 2D pilot sets only the
pressure limit, and that score is the single-constraint value.

MFBO (`kennedy_ohagan_forrester_constrained_ei`): one autoregressive model
`y_HF = ρ y_LF + δ` for LMH and another for dP/L. Forrester scores compare
HF constrained EI with LF constrained EI scaled by cross-fidelity
correlation and the HF/LF cost ratio. The target fidelity is `very_fine`.

Only `valid` CFD results train the GP. `diverged`, `invalid`, and
`execution_failed` are recorded, excluded from training, and not repeated
at the same `(d, L, fidelity)`. They are not turned into LMH = 0 or a
penalty. There is no failure classifier.

The reported design of each method is an evaluated `very_fine` point that
satisfies the pressure limit. An LF prediction is not compared with an HF
evaluation.

## Modes and outputs

`offline_replay` looks up the screening table. A design that is not in the
table is refused. Do not quote replay LMH or cost as the live result.

`live` calls the existing 2D case runner, one Fluent process at a time.
It runs only on the Windows Fluent host.

Each experiment writes, under
`RO_2D_DATA_ROOT/studies/optimization/{hf_bo|mfbo}/<experiment_id>/`:

- `config.json`
- `history.csv`
- `history.json`
- `final_summary.json`

## Windows live commands

Git Bash, after this documentation commit is on `origin/main`. Do not run
the two pilots in parallel.

```bash
cd /c/pyfluent
git fetch origin
git reset --hard origin/main
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_2D_DATA_ROOT="/c/RO_2D_Data"
python scripts/run_ro_2d_hf_bo.py --mode live --experiment-id pilot
python scripts/run_ro_2d_mfbo.py --mode live --experiment-id pilot
python scripts/compare_ro_2d_optimization.py \
  --hf /c/RO_2D_Data/studies/optimization/hf_bo/pilot \
  --mfbo /c/RO_2D_Data/studies/optimization/mfbo/pilot
```

`experiment_id = pilot` has already been run. A repeat reuses valid cases
and still charges their stored solver time, so it does not reproduce a
cold-start cost. The executed histories are in the section above. With a
warm cache the sequential cap is still 6, plus one final `very_fine` check
when the recommended design has no `very_fine` result. Without the four
initial results on disk the caps are 11 new runs (HF-only) and 15 (MFBO).

Offline check, still under `RO_2D_DATA_ROOT`, does not launch Fluent:

```bash
python scripts/run_ro_2d_hf_bo.py --mode offline_replay --experiment-id replay_check
python scripts/run_ro_2d_mfbo.py --mode offline_replay --experiment-id replay_check
```

## Later 3D attachment

The optimizer needs a design box, an `evaluate(design, fidelity)` function,
fidelity names, LMH and dP/L extraction, and a cost model. The pillar
evaluator that supplies that function is below. The loop that calls it is
[3D loop](#3d-loop). It does not replace the 2D design box.

## 3D adapter

`src/ro/mfbo_adapter.py` wraps one pillar case as
`evaluate(design, fidelity, *, run_id, data_root, fidelity_table, drivers)`.
The design is `d_p_mm`, `d_h_mm`, and `d_f_mm`. `run_id` is the operating
point, for example `u0p2_p6M`. `data_root` is required and must not be
`C:/ro_data`. The module does not import Fluent. Geometry, meshing, the
solve, and extraction are the injected drivers, called in that order.

`fidelity_table` maps a name to `m_max`, `m_min`, `m_cpg`, `bl_layers`,
optional `spacer_bl_layers`, and `peel_layers`. The only shipped example
is `EXAMPLE_FIDELITY_TABLE["LF"]`, the production operating mesh
`max085_min006_cpg5_bl4_peel2`. There is no default high-fidelity mesh.
A name that is not in the table raises.

Before CAD, the opening gap
`G = R_p*(pi/4 - asin(r_f/R_p) - asin(r_h/R_p))`
(`r_h = 0` when `d_h = 0`) is compared with that fidelity's `m_min`,
both in millimetres. `0 < G < m_min` returns status `invalid` and reason
`opening_gap_sliver`, and no driver is called.

A leaf is reused only from fields the pipeline writes. Geometry success
is `{geo_id}_meta.json` whose `geo_id` and `inputs` match the design, and
a `.pmdb` whose sha256 equals `pmdb_sha256` (`pillar_cad` does not write
`status`). Mesh success is `mesh_run_record.json` `status` `SUCCESS`
plus `manifest.json`. Run success is `manifest.json` plus
`post/reports/summary_metrics_wide.csv`. A missing field is not success.
A geometry error, a mesh record, or a run that already has solver output
returns `execution_failed` and is not run again. A leaf with no CAD or
Fluent output is an infrastructure failure: it is archived as
`<name>_geometry_failed_<UTC>` (or `mesh_failed` / `run_failed`) and the
next evaluate retries it. The result records `failure_class`.

`lmh` is summary column `lmh_window_module` (window, module-area LMH).
`lmh_module_area` remains the whole-active rescale of `lmh_mass_balance`
and is not a fallback for `lmh`.
`pressure_drop_per_length_pa_per_m` is `pressure_drop_spacer_per_m`.
`cp_average` is `cpc_window_avg_flux`. `cp_q999` is `cp_q999_window_flux`.
`cp_canon_window_avg` is kept as a reference and is not `cp_average`.
The record also carries `cell_count`, mesh wall time, solver wall time,
extraction wall time, and solver time. `convergence_quality` `FAIL` is
`diverged` when `stop_reason` is `diverged`, and `invalid` otherwise.
A failed evaluation is not stored as LMH = 0.

High fidelity is not defined yet.
**Superseded 2026-10-19.** 3D MFBO uses `configs/mfbo_fidelity_table.3d_v1.json`.
`HF_m` is 7 cells, membrane BL 12 / spacer BL 4, HF monitor stop, then
the production convergence gate. `LF_d` is 3 cells, BL 4, LF monitor
stop after `ramp_end` 150. The shipped `EXAMPLE_FIDELITY_TABLE["LF"]`
row above stays the production-mesh example; it is not the v1 screen.

### Host check (recorded 2026-10-08)

The paste reports a check of this adapter on the host, without
launching drivers. It does not name the machine. Data root
`C:/ro_data_mfbo/e2e`. `1b11ec9` recorded that root as unnamed.

Reuse of `MFP_d0900_h0200_f0400` at fidelity `LF`, run id `u0p2_p6M`,
returns `valid`. Returned values: LMH 26.532, module-area LMH 23.74,
dP 79429, cpc 1.0331 after re-extract. `LF` in the shipped table is
the production mesh `max085_min006_cpg5_bl4_peel2`. The paste does not
name the dP column. cpc 1.0331 is a modulus. It is not the CP-excess
(order \(10^{-2}\)) in `docs/MESH_LANDSCAPE.md`. The geometry note that
an earlier paste left `mesh_id` and `run_id` unnamed stays in
`docs/GEOMETRY_DESIGN.md`.

`p80_h20` returns `invalid`, reason `opening_gap_sliver`, \(G = 3.65\,\mu\mathrm{m}\).
That \(G\) is the gap already in `docs/GEOMETRY_DESIGN.md`. This check
does not add a mesh attempt.

**2026-10-08 meeting.** The MFBO objective uses module (installed) area
(`lmh_module_area` above). Report both exposed-area and module-area
LMH; the reporting note is in `docs/metrics_conventions.md`. 3D MFBO
is for the AIChE 2026 presentation only. The benchmark paper comes
first (`docs/AGENTS.md`).

## 3D screen

`scripts/mfbo/run_3d_evaluate.py` runs one pillar design through the
adapter and prints the result record as JSON.
`scripts/mfbo/run_3d_fidelity_screen.py` evaluates every design in a
JSON list at LF and then HF. It writes
`3d_fidelity_screen.csv` and `3d_fidelity_screen.md`. Relative bias is
`(LF − HF) / HF`. Pearson, Spearman, and rank reversals use the same
definitions as the 2D screen (`src/ro_2d_pilot/fidelity_screen.py`):
correlation needs at least three comparable values, and a reversal is a
strict disagreement about which design is better. The quantities are
`lmh`, `lmh_module_area`, `pressure_drop_per_length_pa_per_m`, and
`cp_average` compared as CP−1. Larger LMH is better. Smaller CP−1 and
dP/L are better. The cost ratio is LF wall time divided by HF wall time.
Wall time is `mesh_wall_time_s + solver_wall_time_s +
extraction_wall_time_s`. The reported cost is the median of that ratio.
`--emit-queue` writes a `scripts/mfbo/job_queue.py` queue and does not
evaluate. The host runs that queue one job at a time.

`configs/mfbo_fidelity_table.example.json` sets LF to the production
mesh `max085_min006_cpg5_bl4_peel2`. HF is the string `TBD` and is
refused before any driver starts. A screen needs both levels to be mesh
settings.
**Superseded for the 3D screen (2026-10-19).** The v1 table
`configs/mfbo_fidelity_table.3d_v1.json` names `LF_d` and `HF_m`. The
example file still marks `HF` as `TBD`.

Real drivers are `src/ro/mfbo_drivers.py`. They call
`scripts/generate_pillar_cad.py`, `scripts/mfbo/mesh_mfbo_case.py`,
`scripts/mfbo/solve_mfbo_case.py`, and `scripts/mfbo/reextract_runs.py`
as subprocesses. One job runs at a time. `RO_DATA_ROOT` is set on the
child only. A non-zero exit raises with the child log path.

Mesh knobs (`m_max`, `m_min`, `m_cpg`, `bl_layers`, `spacer_bl_layers`,
`peel_layers`) belong on the mesh CLI.

- `generate_pillar_cad.py` does not take them. It only writes CAD.
- `mesh_mfbo_case.py` did not take them. It now accepts those flags.
  Omitting every flag keeps the previous `--mesh-id` lookup. The default
  mesh id is still `max085_min006_cpg5_bl4_peel2`.
- `solve_mfbo_case.py` takes `--mesh-id`, not the knobs. A mesh id that
  is not in the production solver catalog reuses the default solver case
  and replaces only `mesh_id`. The default mesh id is unchanged.
- `reextract_runs.py` takes a run leaf, not the knobs.

## 3D loop

`scripts/mfbo/run_3d_mfbo.py` runs the pillar problem in
`src/ro/mfbo_3d.py`. Importing either does not launch Fluent. Live mode
imports `ro.mfbo_drivers` and runs one case at a time on the Windows host.

Design variables are pillar diameter `d_p_mm` and bore diameter `d_h_mm`.
Filament diameter `d_f_mm` is fixed at 0.40. The operating point is the
experiment `run_id`, for example `u0p2_p6M`. The objective is maximize
`lmh` (`lmh_window_module`).

Two constraints, both from the experiment file:

```
cp_average - 1 <= cp_excess_max
pressure_drop_per_length_pa_per_m <= dp_max_pa_per_m
```

The design box, budget, initial designs, reference HF wall time, and
LF/HF cost ratio are in that file too. A missing field is an error.
`configs/mfbo_3d_experiment.example.json` sets `cp_excess_max` and
`dp_max_pa_per_m` to `TBD`, and the run refuses those placeholders.
There is no default limit. The example's `reference_hf_wall_s` (3600 s)
and `lf_over_hf_cost_ratio` (0.25) are not a measured 3D screen. The
screened run is `configs/mfbo_3d_experiment.v1.json`.

The same NumPy acquisition is used. Probability of feasibility is the
product of one Gaussian probability per constraint. HF-only queries `HF`.
MFBO is the Kennedy–O'Hagan model on LMH, on dP/L, and on CP excess, with
Forrester's cost ratio taken from the experiment file. With only the
pressure constraint set, the 2D score is unchanged.

Before a candidate is scored, the opening gap is compared with that
fidelity's `m_min`. A sliver, or a bore that is not smaller than the
pillar, is left out of the candidate set and is not evaluated. An initial
design that fails that check is an error.

`diverged`, `invalid`, and `execution_failed` are recorded, excluded from
GP training, and not repeated at the same `(d_p_mm, d_h_mm, fidelity)`.
They are not stored as LMH = 0.

`offline_replay` reads a table keyed by `(d_p_mm, d_h_mm, fidelity)` and
refuses a design that is not in it. `build-replay` builds the LF rows
from campaign pillar leaves in a data root:
`P_p60/80/100` × `h00/15/30` are `d_p_mm` 0.60/0.80/1.00 and `d_h_mm`
0/0.15/0.30, at the requested mesh and `run_id`. A geo_id with no run
leaf is omitted. A leaf that exists is kept, including a failure.

`live` calls `mfbo_adapter.evaluate` with the real drivers. The output
data root must not be `C:/ro_data`. `build-replay` may read a campaign
root; its `--out` path may not be under `C:/ro_data`.

HF-equivalent cost is cumulative wall seconds divided by
`reference_hf_wall_s`. Wall seconds are `mesh_wall_time_s +
solver_wall_time_s + extraction_wall_time_s`. A missing part is imputed
from that fidelity's expected cost and marked `cost_imputed`. Reused
results are still charged. The example budget is 8.0 HF-equivalent and
at most 6 sequential queries, plus one final `HF` evaluation of the
recommended design when that design has no `HF` result yet.

The reported optimum is an evaluated `HF` point that satisfies both
constraints. An LF prediction is not the result. If no evaluated `HF`
point is feasible, the summary fields are null.

Outputs go under
`<data_root>/studies/optimization/3d/{hf_bo|mfbo}/<experiment_id>/`:

- `config.json`
- `history.csv`
- `history.json`
- `final_summary.json`

A restart reloads `history.json` and continues. Raising
`budget_hf_equivalent` or `max_sequential` is allowed. Changing the
limits, the box, the initials, the fidelity table, or the replay table
is refused. A file named `STOP` in the experiment directory finishes the
current evaluation and does not start the next one. Remove it to
continue. If `STOP` is already there and no history exists, the run
writes nothing.

`configs/mfbo_fidelity_table.example.json` still marks `HF` as `TBD`.
A method that needs that level refuses it before any driver starts.

```bash
python scripts/mfbo/run_3d_mfbo.py build-replay \
  --data-root "$RO_DATA_ROOT" \
  --run-id u0p2_p6M \
  --mesh-id max085_min006_cpg5_bl4_peel2 \
  --fidelity LF \
  --out /c/ro_data_mfbo/studies/optimization/3d/campaign_lf_replay.json

python scripts/mfbo/run_3d_mfbo.py run \
  --config configs/mfbo_3d_experiment.example.json \
  --data-root /c/ro_data_mfbo
```

The example run refuses until `cp_excess_max` and `dp_max_pa_per_m` are
finite numbers, the fidelity table has mesh settings for every level the
method queries, and, for `offline_replay`, the replay table exists.

## 3D v1 experiment

`configs/mfbo_3d_experiment.v1.json` is the screened pillar problem.
In the loop, `LF` is `LF_d` and `HF` is `HF_m` from
`configs/mfbo_fidelity_table.3d_v1.json`. The limits are CP excess 0.040
and 71000 Pa/m. `lf_over_hf_cost_ratio` is the screen median wall-time
ratio 0.54. `cost_proxy_ratio` 0.25 is the screen median of cell count
times iterations; acquisition uses the wall-time ratio.
`reference_hf_wall_s` is `screen_median`, the median HF wall time
(mesh + solver + extraction) in
`studies/fidelity_screen_3d_v1/3d_fidelity_screen_v1.csv` under the data
root. `budget_hf_equivalent` is `11h`: 11 hours divided by that median,
inside a 10-12 h wall-time window. The seed is 0. `max_sequential` is 24.

Both methods use that file. `--method` selects `hf_bo` or `mfbo`.
`hf_bo` evaluates four HF designs: `(0.80, 0.15)`, `(0.60, 0.00)`,
`(1.00, 0.30)`, and `(0.60, 0.30)`. `mfbo` evaluates those same four at
HF and at LF. `(1.00, 0.00)` stays in the design box. Its screen HF
solve failed the gate after 2000 iterations (6355 s, about 3.7 times the
median HF wall time), so it is not an initial design. A later proposal
there is recorded, kept out of the GP, and not repeated, as in the 2D
loop. A leaf already on disk is reused and charged.

`scripts/mfbo/landscape_3d.py` fits the loop's Gaussian process to valid
HF points (and, for the figure, to valid LF points) and writes
`landscape_3d_lmh.png` with both constraint lines. `--dry-run` validates
the experiment file, checks the opening gap on the initial designs, and
prints the plan and any recorded leaf costs. It does not launch Fluent.
`emit-queue` writes two `job_queue` jobs, `hf_bo` then `mfbo`, each with
`--geometry-worker`.

```bash
python scripts/mfbo/landscape_3d.py \
  --csv /c/ro_data_mfbo/mfbo3d/studies/fidelity_screen_3d_v1/3d_fidelity_screen_v1.csv \
  --out-dir /c/ro_data_mfbo/mfbo3d/studies/fidelity_screen_3d_v1/landscape

python scripts/mfbo/run_3d_mfbo.py run \
  --config configs/mfbo_3d_experiment.v1.json \
  --data-root /c/ro_data_mfbo/mfbo3d \
  --dry-run

python scripts/mfbo/run_3d_mfbo.py emit-queue \
  --config configs/mfbo_3d_experiment.v1.json \
  --data-root /c/ro_data_mfbo/mfbo3d \
  --out /c/ro_data_mfbo/mfbo3d/studies/optimization/3d/v1_queue.json

python scripts/mfbo/job_queue.py run \
  /c/ro_data_mfbo/mfbo3d/studies/optimization/3d/v1_queue.json
```

## 3D v2 experiment

`configs/mfbo_3d_experiment.v2.json` keeps the v1 box, seed 0, run id,
fidelity table, and the same four HF and LF initial points. The candidate
grid is 17 by 17. `cp_excess_max` stays 0.040. `dp_max_pa_per_m` is 72000.

On that grid, 71000 Pa/m predicts 6 feasible points, all on the
near-contact diagonal, where `|G|` is a few micrometres up to about 20 µm.
None of the 9 valid `hf_bo` v1 HF points were feasible at 71000. At 72000
the v1 points `(0.75, 0.0375)` (CP-1 0.0398, dP/L 71595 Pa/m, LMH 24.40)
and `(0.75, 0.075)` (CP-1 0.0397, dP/L 71749 Pa/m, LMH 24.41) become
feasible. The config `note` records that choice.

`experiment_id` is `v2`, so `hf_bo` and `mfbo` each start with an empty
history under `studies/optimization/3d/{hf_bo|mfbo}/v2`. They do not
continue the v1 runs.

```bash
python scripts/mfbo/run_3d_mfbo.py run \
  --config configs/mfbo_3d_experiment.v2.json \
  --data-root /c/ro_data_mfbo/mfbo3d \
  --dry-run

python scripts/mfbo/run_3d_mfbo.py emit-queue \
  --config configs/mfbo_3d_experiment.v2.json \
  --data-root /c/ro_data_mfbo/mfbo3d \
  --out /c/ro_data_mfbo/mfbo3d/studies/optimization/3d/v2_queue.json

python scripts/mfbo/job_queue.py run \
  /c/ro_data_mfbo/mfbo3d/studies/optimization/3d/v2_queue.json
```

## 3D fidelities `HF_m` and `LF_d`

`configs/mfbo_fidelity_table.3d_v1.json`. Mesh ids from
`format_production_mesh_id` (`src/ro/campaign_matrix.py:66–84`).
`ramp_end` is `#define RAMP_ITER_3` in the compiled case UDF
(`udfs/260822_RO_UDF.c:61`; `src/ro/lf_monitor_stop.py:6–10`). It is
not `configs/run_config.py` `ramp_full_iteration`.

| | `HF_m` | `LF_d` |
|---|---|---|
| `n_active` | 7 (`:31`) | 3 (`:8`) |
| mesh | `max085_min006_cpg5_bl12s4_peel2` (`bl_layers` 12, `spacer_bl_layers` 4, `:28–29`) | `max085_min006_cpg5_bl4_peel2` (`bl_layers` 4, `:6`) |
| window | `window_source` `family_default` (`:32`) | `n_lead_excluded` 1 (`:9`) |
| stop | `hf_monitor_stop` (`tol_lmh` 1e-4, `tol_dp` 1e-4, `n` 200, `continuity_max` 1e-5, `:37–42`) | `lf_monitor_stop` after `ramp_end` 150 (`tol_lmh` 0.001, `tol_dp` 0.005, `n` 100, `:14–18`) |
| gate | production (`convergence_gate` omitted; `src/ro/mfbo_fidelity.py:378–380`, `:455–472`) | `lf_monitor` (`:20–22`) |
| extract | `--profile mfbo` | `--profile mfbo` |
| run-id suffix | `hfm` | `lfd` |

`hf_monitor_stop` cannot take `convergence_gate` mode `lf_monitor`
(`src/ro/mfbo_fidelity.py:425–428`). The production gate is
`src/ro/convergence_quality.py:30–32`.

## 3D fidelity screen v1 (measured)

Data root
`C:/ro_data_mfbo/mfbo3d/studies/fidelity_screen_3d_v1/3d_fidelity_screen_v1.csv`.
Nine designs \(D_p \in \{0.6, 0.8, 1.0\}\) mm × \(d_h \in \{0, 0.15, 0.30\}\)
mm, \(d_f = 0.40\) mm, `u0p2_p6M`. Eight valid at both `HF_m` and `LF_d`.
\(D_p = 1.0\), \(d_h = 0\) is HF invalid (gate FAIL after 2000 iterations)
and LF valid. **From host report 2026-10-16**, restated 2026-10-19.
This checkout cannot re-read the CSV.

Over the eight valid pairs (`src/ro/mfbo_fidelity_screen.py:1–10`):

| | value |
|---|---|
| Spearman, LMH | 1.00 |
| Spearman, dP/L | 1.00 |
| Spearman, CP−1 | 1.00 |
| CP−1 bias (LF − HF) / HF | −9.1% ± 2.9% |
| wall-time ratio median (LF/HF) | 0.54 |
| cell × iteration ratio | 0.25 |

Wall time is mesh + solver + extraction
(`src/ro/mfbo_fidelity_screen.py:8–9`). Acquisition uses the wall-time
ratio (`configs/mfbo_3d_experiment.v1.json:17`; `cost_proxy_ratio` 0.25
is the cell × iteration number, `:18`).

## 3D v1 live run (lessons)

`configs/mfbo_3d_experiment.v1.json`, `candidate_grid` 9 (`:20`),
`dp_max_pa_per_m` 71000 (`:13`), data root `C:/ro_data_mfbo/mfbo3d`.
**From host report 2026-10-17.** `hf_bo` finished in about 5.5 h of an
11 h budget: 17 sequential queries, 8 of them
`execution_failed` / `leaf_not_successful` with 0 s wall in
`history.csv`, yet cumulative wall charged a reference HF per failure.
No feasible point at 71000 Pa/m. Near-feasible examples:
`(0.75, 0.0375)` CP−1 0.0398, dP/L 71595 Pa/m, LMH 24.40;
`(0.75, 0.075)` CP−1 0.0397, dP/L 71749 Pa/m, LMH 24.41.

Lessons used in v2: the 9 × 9 grid is too coarse; infrastructure
failures must not be charged as a reference HF
(`src/ro/mfbo_3d.py:15–17`). Discovery parents need their own console
so a queue log cannot hide the backend (commit `2671300`;
`src/ro/discovery_session.py:22–26`, `:48–55`).

## 3D v2 `hf_bo` result

`configs/mfbo_3d_experiment.v2.json`: 17 × 17 grid (`:22`), CP excess
≤ 0.040 (`:13`), dP/L ≤ 72000 Pa/m (`:14`), 11 h HF-equivalent budget
(`:16`), `geometry` `pregenerated` (`:5`). Generate time is still
charged from `<geo_id>_generate_wall_time.json`
(`src/ro/mfbo_adapter.py:267–269`; `scripts/mfbo/pregenerate_grid.py:9`).
Pregeneration is commit `2945b51`. Data root
`C:/ro_data_mfbo/mfbo3d/studies/optimization/3d/hf_bo/v2/`.

**From host report 2026-10-19.** Optimum \(D_p = 0.75\) mm,
\(d_h = 0.05625\) mm, LMH 24.414. Both constraints nearly active.
The run used 14 HF evaluations and about 22.14 HF-equivalent.
`mfbo` v2 used 13 HF and 10 LF and finished at about 25.98
HF-equivalent. The budget is about 22.98. See **3D seeds** and
**3D budget** below. This checkout cannot read `final_summary.json`.

## 3D seeds

`seed` 0 uses the four initial points in the experiment file. That is
the v2 design, so a seed-0 run stays the one already recorded.
`hf_bo` and `mfbo` share those points, and `mfbo` evaluates each at LF.

Any other live seed draws that many points with a maximin Latin
hypercube on the candidate-grid points that pass the opening-gap
check (`draw_maximin_lhs` in `src/ro/mfbo_3d.py`). The draw does not
depend on the method, so the two methods still share the initial
design. Each axis of the grid is split into four contiguous index
strata, a permutation pairs the strata, and one valid grid point is
taken from each cell. The sample with the largest minimum pairwise
distance in the unit design box is kept. Replay mode keeps the file
points, because its candidate set is the replay table.

An exact tie in the acquisition argmax keeps the first grid candidate
when the seed is 0. Any other seed breaks the tie with a stable hash
of `(seed, d_p_mm, d_h_mm, fidelity)`. The hash does not change as
other candidates are evaluated.

A replicate is a copy of `configs/mfbo_3d_experiment.v2.json` with a
new `experiment_id` (for example `v2_seed1`) and `seed` set to that
replicate. The initial points left in the file are the seed-0 design;
a live non-zero seed does not evaluate them.

## 3D budget

The rule in use is `budget_stop` `expected`. That is the default when
the field is omitted, and it is the rule the v2 runs used. A sequential
query starts only when the cumulative HF-equivalent plus the expected
cost still fits. Expected HF cost is 1. Expected LF cost is
`lf_over_hf_cost_ratio` (0.54). The query is then charged its recorded
wall time, not that expected cost. The final HF recommendation is
outside this gate: it is still evaluated and charged.

A gate-failing HF run can cost several times one HF-equivalent.
`mfbo` v2 finished at about 25.98 HF-equivalent against the 11 h budget
of about 22.98 because a gate-failing HF run cost more than the
expected 1. `hf_bo` v2 finished under the budget, at about 22.14.
The excess is the recorded wall time. It is not an imputed reference HF.

`budget_stop` `cumulative` starts a sequential query, including the
final HF recommendation, only while the cumulative cost is still below
the budget. The query that crosses the budget is charged its full
recorded wall time, and the loop then stops. That overshoot stays in
the history. It is not clipped to the budget and it is not replaced
by the expected cost.

Compare methods at equal budget by the best feasible HF LMH against
cumulative HF-equivalent, not by the final totals and not by the number
of evaluations. Read both curves at the same cumulative cost. A point
past the budget is the recorded cost of the query that crossed it.

```bash
python scripts/mfbo/plot_budget_curves.py \
  --out-csv curves.csv \
  --out-png curves.png \
  "$RO_DATA_ROOT/studies/optimization/3d/hf_bo/v2" \
  "$RO_DATA_ROOT/studies/optimization/3d/mfbo/v2"
```

## Infrastructure (2026-10-18 .. 2026-10-19)

| | commit | source |
|---|---|---|
| Discovery parent on its own console; a queue log does not inherit stdout/stderr | `2671300` | `src/ro/discovery_session.py:22–26`, `:48–69` |
| Pregenerate the 17 × 17 grid so the loop does not launch Discovery | `2945b51` | `scripts/mfbo/pregenerate_grid.py:1–9`; `configs/mfbo_3d_experiment.v2.json:5` |
| Crashed solve retries once; only the Fluent PID recorded at that launch is stopped | `6220d39` | `src/ro/session_retry.py:73–88`, `:104–125`; `src/ro/mfbo_adapter.py:18–20` |
