# Reverse-osmosis spacer CFD campaign

Systematic CFD comparison of reverse-osmosis membrane feed spacer designs.
Ansys Fluent 25.1 is driven by PyFluent. The campaign mesh set is built
(31 geometries); the 279-run solver sweep is the next stage.

## Campaign

Four spacer families — diamond, multi-layer, pillar, and sinusoidal — plus an
empty reference channel. 31 geometries, each at nine operating points (inlet
velocity 0.1 / 0.2 / 0.3 m/s crossed with outlet gauge pressure 4 / 6 / 8 MPa)
→ 279 solver runs.

All 31 meshes are built at campaign settings `max085_min006_cpg5_bl4_peel2`
(D0817_a60 uses `m_max` 0.060). The diamond reference remains `D2450_a45` on
that mesh (796,009 cells). Historical runs under `RO_DATA_ROOT/archive/` are
not read.

## 2D methodological pilot

A separate 2D channel study lives in `src/ro_2d_pilot/`. Its optimization
layer is an HF-only Bayesian optimization baseline and a cost-aware
multi-fidelity pilot. Data go under absolute `RO_2D_DATA_ROOT`, not
`RO_DATA_ROOT`. The problem, the 30000 Pa/m pressure constraint, the
shared initial designs, the HF-equivalent budget, and the Windows commands
are in [docs/RO_2D_OPTIMIZATION.md](docs/RO_2D_OPTIMIZATION.md). The live
`pilot` comparison has been run there. This does not implement 3D MFBO.

## Pipeline

No manual steps in the loop. Orchestrators (`batch_meshing.py`,
`batch_solver_sweep.py`) launch one Fluent subprocess per case via
`PYFLUENT_RUN_OVERRIDES`.

1. **Meshing.** `scripts/meshing_code_260616.py` imports `{geo_id}.dsco`
   and writes `{geo_id}_{mesh_id}.msh.h5`.
2. **Quality gate.** Orthogonal quality, aspect ratio, skewness, and
   skewed-face fraction are parsed from the transcript; a failing mesh is
   refused.
3. **Solve.** `scripts/solver_code_260616.py` loads a template case,
   compiles the dated UDF into the run folder, and iterates (residual
   1e-7, cap 2000). Stop reason is classified from the transcript onto
   the run manifest.
4. **Post-processing.** `scripts/batch_postprocess_all_cases.py` extracts
   reports, PyEnSight contours, and shear contours. Layout comes from the
   mesh manifest.

## Architecture

Code and data are physically separated. The git tree holds the package,
scripts, configs, templates, and UDFs. Geometries, meshes, runs, and inventory
live under `RO_DATA_ROOT`, which must be an absolute path. Unset or relative
raises; it never defaults to the repository.

```
RO_DATA_ROOT/
  geometries/{family}/{geo_id}/
  meshes/{family}/{geo_id}/{mesh_id}/
  runs/{family}/{geo_id}/{mesh_id}/{run_id}/
  inventory/
  archive/          # frozen legacy; no code reads this
```

Every mesh and run leaf carries a `manifest.json`. Directory names are labels
for humans. Parameters — including values absent from the path, such as
`bridge_radius_m` and `overlap_m` — are read from the JSON. Leaves are found
by locating `manifest.json`; folder names are never parsed for `u`, `p`, or
mesh knobs.

Paths go only through `ro.paths.geometry_dir`, `mesh_dir`, and `run_dir`.
Ids are validated (`diamond|ml|pillar|sin|empty`; mesh ids like
`max085_min006_cpg5_bl4_peel2`; run ids like `u0p2_p6M`). A typo raises
`ValueError` instead of creating a parallel tree that a later glob would treat
as a real case.

Failures are loud. A leaf without a valid manifest is refused. Manifest ids
must match the directory components and the builder path, or the read raises
`ManifestError`. Required fields have no defaults. Quality fields may be null
only before `mesh_sha256` is set. `run_config.py` uses an explicit `REQUIRED`
placeholder, so an unset field fails validation rather than falling through.

## Metrics and conventions

Campaign CP uses the **canonical** Bae (2023) form with a mid-plane bulk
denominator \(c_b\) over the evaluation window:

\[
M = (c_m - c_p)/(c_b - c_p)
\]

Literature variants L1 (Gu 2017) and L2 (Bae inlet approximation) are computed
alongside for the definition-sensitivity table only. Averaging order, rescaling
from UDM-9, guards, and window rules are documented in
[docs/metrics_conventions.md](docs/metrics_conventions.md).

## UDF

Production source is `udfs/260822_RO_UDF.c`. Membrane water and salt flux are
cell source terms on `wall_top_mem` / `wall_bottom_mem`. Wall concentration is
reconstructed from the first-cell centre by film theory
(`RO_ANALYTIC_CWALL = 1`):

```
c_wall = c_p + (c_1 - c_p) * exp(Jw * y1 / D)
```

Thirteen UDM slots hold the sinks, fluxes, wall concentration, film-theory CP,
membrane area, and wall-to-centroid distance `y1`. Dated UDF files stay
byte-identical once they have produced results; a physics or layout change
gets a new dated file.

The campaign inlet is a self-normalizing plane-Poiseuille profile in *z*
(membranes at constant *z*; spanwise periodic). The discrete integration
factor

```
G = Σ 6 η_i (1 − η_i) dA_i / Σ dA_i ,   η = (z − z_bottom) / H
```

is measured per mesh at runtime (`RO_UDF_INLET_PROFILE_G` in `fluent-*.trn`).
The first run writes `G` into the mesh manifest. Later runs compare at 1e-6
relative and abort before `iterate` on mismatch. `G` is not copied across
`mesh_id`s.

## Tests

Full suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`
(local helper: `scripts/run_full_pytest.sh`). WSL measured 2026-09-11:
**1059 passed, 1 skipped, 0 failed**. The skip is
`tests/test_campaign_geo_ids.py` when `RO_DATA_ROOT` is unset. Windows is a
separate host run. The suite is pure Python: no Fluent, no real `RO_DATA_ROOT`.
It covers path
builders and id grammar, manifest read/write and stale-path refusal, domain
layout from the mesh manifest, UDM index parity with the C enum, UDF
patching, inlet-`G` parse-and-assert, stop-reason classification, inventory
and post-processing wiring, and import safety (every `scripts/` entry point
must import with `RO_DATA_ROOT` unset). The meshing–solve–post loop is
exercised on the Windows server.

## Layout and a single case

```
src/ro/       installable helpers (paths, manifests, layout, solver commons)
scripts/      workers and orchestrators
configs/      run_config.py, batch_config.py, post_config.py, …
udfs/         dated *_RO_UDF.c
templates/    Fluent case / CFF templates
tests/
docs/
```

CLI scripts need `pip install -e .` (`pythonpath = ["src"]` covers pytest
only). Fluent runs on the Windows server, not in WSL. Git Bash session:

```bash
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
```

Post-process one finished run (dry-run first):

```bash
python scripts/batch_postprocess_all_cases.py \
  --family diamond \
  --geo-id D2450_a45 \
  --mesh-id max085_min006_cpg5_bl4_peel2 \
  --run-id u0p2_p6M \
  --run-reports \
  --dry-run
```

Selectors are `--family --geo-id --mesh-id --run-id`. A complete four-id
selection resolves through `run_dir()` and refuses a missing manifest.
`python scripts/case_inventory.py` scans `RO_DATA_ROOT/runs`; a missing tree
is an error, not "Total cases: 0". Server procedure: `docs/DEPLOY_RUNBOOK.md`.
