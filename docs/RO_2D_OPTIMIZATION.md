# 2D RO optimization pilot

Methodological pilot only. It is not the 3D campaign, and `very_fine` is
the target fidelity for this pilot, not a grid-independent solution.

The 3D production workflow (`src/ro/`, `scripts/solver_code_260616.py`,
`udfs/260822_RO_UDF.c`, `RO_DATA_ROOT`) is unchanged. A 3D multi-fidelity
Bayesian optimization layer is still not implemented. This document records
the 2D closed loop that can later be attached to a separate 3D evaluator.

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
improvement times the probability that dP/L is within the limit.

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
evaluator that supplies that function is below. It does not replace the
2D design box.

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
A leaf that exists without that record returns `execution_failed` and
the leaf path, and is not run again.

`lmh` is `lmh_mass_balance` per exposed membrane area.
`lmh_module_area` rescales it with the same module-area formula as
`scripts/mfbo/summarize_results.py`.
`pressure_drop_per_length_pa_per_m` is `pressure_drop_spacer_per_m`.
`cp_average` is `cpc_window_avg_flux`. `cp_q999` is `cp_q999_window_flux`.
`cp_canon_window_avg` is kept as a reference and is not `cp_average`.
The record also carries `cell_count`, mesh wall time, solver wall time,
extraction wall time, and solver time. `convergence_quality` `FAIL` is
`diverged` when `stop_reason` is `diverged`, and `invalid` otherwise.
A failed evaluation is not stored as LMH = 0.

High fidelity is not defined yet.

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
