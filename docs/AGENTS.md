# AGENTS.md

Guidance for AI coding agents (Cursor, Codex, Claude Code) working in this repo.
Read `REVIEW.md` for the findings history (F-01 … F-21). That file is a
historical record of the July 2026 review; the live tree is this document plus
`docs/RESTRUCTURE_PLAN.md`.

## Project

RO-membrane CFD automation. Ansys Fluent 25.1 driven via PyFluent + PyEnSight;
membrane physics live in a compiled C UDF (`udfs/*.c`). Pipeline: case matrices
in `configs/batch_config.py` → subprocess workers (`scripts/meshing_code_*.py`,
`scripts/solver_code_*.py`) → `RO_DATA_ROOT/runs/{family}/{geo_id}/{mesh_id}/{run_id}/`
→ post-processing orchestrators under `scripts/`. A multi-fidelity Bayesian
optimization (MFBO) layer is planned but not implemented — keep it in mind,
don't build it unasked.

Production UDF is `260816_RO_UDF.c`. `RO_ANALYTIC_CWALL = 1` is the production
setting. The 0.44% LMH and 7.1% CP-excess figures (and the UDF header's
"~0.4% against 2.7%") are a bl4-vs-bl6 wall-normal comparison at fixed
`max085`, not a surface-size (`m_max`) study. Surface-size grid
independence is not closed.

The D2450_a45 peel2 remesh matched the archive ledger (796,009 cells, ortho
0.102087, AR 62.7715, skew 0.67063399). The restructure is physics-neutral.

## Environment (critical)

- Code is edited in **WSL (Linux)**. There is **NO Ansys Fluent/EnSight here,
  and never will be.**
- Production runs on a **separate Windows server** (Git Bash, repo at
  `/c/pyfluent`) that pulls this same git repo independently.
- Sync is via `git push` / `git pull`. A fix takes effect on the server only
  after the user explicitly pulls, on their schedule.
- **You cannot run Fluent or EnSight.** Verify only via static analysis,
  pure-Python tests, and `batch_solver_rerun --dry-run` (which the user runs on
  the server). Never install, mock-invoke, or simulate Ansys tooling.

## Cross-platform: tests MUST pass on WSL (Linux) AND the Windows server

This has bitten us repeatedly (PosixPath INTERNALERROR, host-dependent
assertions, real drive paths). The server pytest is the safety net; write
tests that pass on both from the start.

- Never instantiate `pathlib.PosixPath` / `WindowsPath` directly. Use `Path`,
  or `PurePosixPath` / `PureWindowsPath` for string-shape logic that must work
  on any OS.
- Never let a test depend on the host OS implicitly (`os.name`, real
  `C:\...` paths, `data_root()` of the running machine). Pin the host OS in
  the test — `monkeypatch` `os.name`, or pass an explicit `host_os_name=` — so
  behavior is deterministic on both platforms.
- Guard OS-specific filesystem ops (`os.symlink`, etc.) with
  `@pytest.mark.skipif(...)` and a clear reason. Skip, don't fail.
- Use `tmp_path` for real filesystem paths in tests; never hardcode POSIX
  strings like `/data/...`. Never call `data_root()` at import.

## Hard constraints

- **Preserve external behavior.** Env-var interfaces (`PYFLUENT_RUN_OVERRIDES`,
  `PYFLUENT_RUN_CONFIG`, `PYFLUENT_POST_CONFIG`, `PYFLUENT_POST_OVERRIDES`,
  `PYFLUENT_SKIP_VALIDATION`, `RO_DATA_ROOT`, `PYFLUENT_PROJECT_ROOT`) and
  config key names stay unchanged unless explicitly approved.
- **Do not silently rename or drop CSV/JSON columns.** Additive columns from
  the restructure (`family`, `geo_id`, `mesh_id`, `run_id`) are in place.
  Inventory still emits `geo_name` / `case_name` as aliases of `geo_id` /
  `run_id`. Compact inventory has the four ids plus `case_dir`.
- **Artifact names** are `{geo_id}_{run_id}_final.{cas,dat}.h5` and
  `{geo_id}_{mesh_id}.msh.h5`. Mesh identity is in the path and the manifest,
  not a `__mesh_*` filename suffix. Inventory and post refuse a glob fallback
  when those names disagree with files on disk.
- **UDF files are dated and immutable once used for results.** Any change to
  UDF physics, to the UDM layout, or to the meaning of an existing hook gets
  a **new dated file** under `udfs/` (e.g. `260822_RO_UDF.c`), never an
  in-place edit of an existing one. Point `run_config.udf_source_file_name`
  at the new file. Rationale: `260810_RO_UDF.c` was overwritten three times
  after results had already been produced with it, so the filename no longer
  identified a single behaviour. Bug fixes that do not change physics, UDM
  indices, or hook semantics may stay in the same file as ordinary commits.
  Leave older dated files byte-identical; they are regression references.
- **Do not modify** the `.scm` template's computed values. Geometries live
  under `RO_DATA_ROOT/geometries/`; do not invent a repo-side `00_Geometries/`
  tree or write CAD into git.
- **Dated worker filenames stay.** `meshing_code_260616.py`,
  `solver_code_260616.py`, and dated `*_RO_UDF.c` keep their names. Orchestration
  scripts already dropped numeric prefixes (`case_inventory.py`,
  `batch_postprocess_all_cases.py`, `batch_solver_rerun.py`); do not rename
  those again without an explicit step.
- **Path construction** goes through `ro.paths` builders. There is no
  `results_root()` and no `03_Results`. Orchestrator selectors are
  `--family --geo-id --mesh-id --run-id`. Worker `--geo-name` / `--case-name`
  are filename labels equal to the ids.
- Validators are **input-only**: never change behavior for valid inputs, only
  reject genuinely bad input.

## Workflow

- On non-trivial tasks, **propose a plan and wait for approval** before editing
  code.
- **Small commits, one concern each.** Keep code commits separate from docs
  (`REVIEW.md`) commits.
- If anything is ambiguous or risky, **stop and ask** instead of guessing.
- **Parity harness for refactors:** before extracting/moving shared logic, keep
  the old inline implementation reachable in the test and assert `old == new`
  on real inputs BEFORE deleting the inline copy.
- **Classify changes SAFE vs RISKY.** SAFE = pure Python, unit-testable, no
  live session. RISKY = touches live PyFluent/EnSight or recovery/strategy
  logic — do these only with server validation, never trust static analysis
  alone.
- **Validate findings against current-campaign data before scoping a fix.**
  Legacy/defunct cases can create false universal patterns (see F-20: a
  "systematic API bug" turned out to affect only legacy cases lacking a report
  CSV).

## Verification

- Run the full pytest suite; it must stay green on both platforms
  (WSL **784 passed**; Windows without symlink privilege **783 passed, 1 skipped**).
- Preserve exit-code contracts: `0` success, `1` exception/preflight, `2`
  failure (post-processing partial = `1` WARN, total = `2`; solver artifact
  failure = `2`).
- For anything touching live Fluent behavior, provide the exact server command
  (dry-run first) and state what output proves success. Server commands live
  in `docs/DEPLOY_RUNBOOK.md`.

## Shared module

`src/ro/solver_common.py` holds shared pure helpers: paths, case naming,
input-mode, artifact-exit, and convergence math. Prefer importing `from ro.x`
over duplicating logic. Note two deliberately distinct path helpers — do not
conflate them:
- `path_to_fluent_str` — `os.path.abspath` + forward slashes (solver/batch
  semantics).
- `path_to_fluent_str_resolved` — `Path.resolve()` + forward slashes
  (07 / symlink-aware semantics).

`strip_mesh_suffix` is deleted. `is_matrix_base_case_name` remains for 07
candidate selection. Layout comes from the mesh manifest
(`layout_from_mesh_manifest`); `resolve_layout(geo, mesh)` raises.
