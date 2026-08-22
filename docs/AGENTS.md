# AGENTS.md — My_CFD_Project

Guidance for AI coding agents (Cursor, Codex, Claude Code) working in this repo.
Read `REVIEW.md` for the full findings history (F-01 … F-21, many resolved).

## Project

RO-membrane CFD automation. Ansys Fluent 25.1 driven via PyFluent + PyEnSight;
membrane physics live in a compiled C UDF (`02_UDFs/*.c`). Pipeline: case
matrices in `batch_config.py` → subprocess workers (`meshing_code_*.py`,
`solver_code_*.py`) → `03_Results/<geo>/<case>/` → post-processing under
`01_Scripts/post_processing/`. A multi-fidelity Bayesian optimization (MFBO)
layer is planned but not implemented — keep it in mind, don't build it unasked.

Production UDF is `260815_RO_UDF.c`. `RO_ANALYTIC_CWALL = 1` is the production
setting. CP is grid-independent to about 0.4% against geometry-to-geometry
differences of 2.7%. The grid-convergence question is closed.

## Environment (critical)

- Code is edited in **WSL (Linux)**. There is **NO Ansys Fluent/EnSight here,
  and never will be.**
- Production runs on a **separate Windows server** (Git Bash, repo at
  `/c/PyFluent`) that pulls this same git repo independently.
- Sync is via `git push` / `git pull`. A fix takes effect on the server only
  after the user explicitly pulls, on their schedule.
- **You cannot run Fluent or EnSight.** Verify only via static analysis,
  pure-Python tests, and `07 --dry-run` (which the user runs on the server).
  Never install, mock-invoke, or simulate Ansys tooling.

## Cross-platform: tests MUST pass on WSL (Linux) AND the Windows server

This has bitten us repeatedly (PosixPath INTERNALERROR, host-dependent
assertions, real drive paths). The server pytest is the safety net; write
tests that pass on both from the start.

- Never instantiate `pathlib.PosixPath` / `WindowsPath` directly. Use `Path`,
  or `PurePosixPath` / `PureWindowsPath` for string-shape logic that must work
  on any OS.
- Never let a test depend on the host OS implicitly (`os.name`, real
  `C:\...` paths, `results_root` of the running machine). Pin the host OS in
  the test — `monkeypatch` `os.name`, or pass an explicit `host_os_name=` — so
  behavior is deterministic on both platforms.
- Guard OS-specific filesystem ops (`os.symlink`, etc.) with
  `@pytest.mark.skipif(...)` and a clear reason. Skip, don't fail.
- Use `tmp_path` for real filesystem paths in tests; never hardcode POSIX
  strings like `/data/...`.

## Hard constraints

- **Preserve external behavior.** Env-var interfaces (`PYFLUENT_RUN_OVERRIDES`,
  `PYFLUENT_RUN_CONFIG`, `PYFLUENT_POST_CONFIG`, `PYFLUENT_POST_OVERRIDES`,
  `PYFLUENT_SKIP_VALIDATION`), config key names, the `03_Results/<geo>/<case>/`
  tree, case-naming (`u0p1_p4M__<mesh>` style), and all CSV/JSON output
  schemas (column names/order/count **byte-identical**) and output filenames
  must stay unchanged unless explicitly approved.
- **UDF files are dated and immutable once used for results.** Any change to
  UDF physics, to the UDM layout, or to the meaning of an existing hook gets
  a **new dated file** under `02_UDFs/` (e.g. `260813_RO_UDF.c`), never an
  in-place edit of an existing one. Point `run_config.udf_source_file_name`
  at the new file. Rationale: `260810_RO_UDF.c` was overwritten three times
  after results had already been produced with it, so the filename no longer
  identified a single behaviour. Bug fixes that do not change physics, UDM
  indices, or hook semantics may stay in the same file as ordinary commits.
  Leave older dated files byte-identical; they are regression references.
- **Do not modify** the `.scm` template's computed values, or anything in
  `00_Geometries/`.
- **Refactor by extraction, not rewrite.** Keep entry-point script names
  stable (no renames of `07_batch_solver_rerun.py`, `solver_code_*.py`, etc.).
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

- Run the full pytest suite; it must stay green on both platforms.
- Preserve exit-code contracts: `0` success, `1` exception/preflight, `2`
  failure (post-processing partial = `1` WARN, total = `2`; solver artifact
  failure = `2`).
- For anything touching live Fluent behavior, provide the exact server command
  (dry-run first) and state what output proves success.

## Shared module

`01_Scripts/_solver_common.py` holds shared pure helpers: paths, case naming,
input-mode, artifact-exit, and convergence math. Prefer importing from it over
duplicating logic. Note two deliberately distinct path helpers — do not
conflate them:
- `path_to_fluent_str` — `os.path.abspath` + forward slashes (solver/batch
  semantics).
- `path_to_fluent_str_resolved` — `Path.resolve()` + forward slashes
  (07 / symlink-aware semantics).

Staged-but-unwired helpers exist for future fixes (e.g. `strip_mesh_suffix`,
`is_matrix_base_case_name`); don't wire them into new behavior without approval.
