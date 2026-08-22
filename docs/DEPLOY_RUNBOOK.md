# Server deploy runbook — post-campaign fixes (2026-07-20)

Paste these commands into **Git Bash on the Windows server**. Assumptions:

- Repo root: `/c/PyFluent`
- Python venv already created at `.venv` (activate each session)
- You have **pushed the 8 WSL commits** to the remote before step 1
- Long-running steps launch **Fluent / PyEnSight / PyFluent** — run on the licensed server, not WSL

---

## 0. Session setup (every Git Bash window)

```bash
cd /c/PyFluent
source .venv/Scripts/activate
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
```

Optional: confirm branch and commits:

```bash
git fetch origin
git log --oneline -8 HEAD
```

---

## 1. Pull fixed code

**Duration:** seconds

```bash
cd /c/PyFluent
git pull origin main
```

If you use a different default branch, adjust `main`.

---

## 2. Sanity check — test suite once

**Duration:** under 1 minute

```bash
cd /c/PyFluent
source .venv/Scripts/activate
python -m pip install -q pytest
python -m pytest tests/ -v
```

Expect **86 passed**. Any failure means the pull is incomplete or the venv is wrong — do not proceed.

---

## 3. Re-run case inventory (fixed F-02 defaults)

**Duration:** ~2–10 minutes (filesystem scan only; no Fluent)

### `--max-iter` default

`00_case_inventory.py` reads `My_CFD_Project/01_Scripts/batch_config.py` →
`common_solver_settings.max_iterations`, which is **1000** for the deadline campaign.
You do **not** need to pass `--max-iter` unless you want an explicit override.

Confirm what the default resolves to on the server:

```bash
cd /c/PyFluent
python - <<'PY'
import importlib.util
from pathlib import Path
p = Path("My_CFD_Project/01_Scripts/batch_config.py")
spec = importlib.util.spec_from_file_location("bc", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print("max_iterations =", m.common_solver_settings["max_iterations"])
PY
```

### Run inventory

```bash
cd /c/PyFluent
python My_CFD_Project/01_Scripts/post_processing/00_case_inventory.py
```

No extra flags required if `03_Results` lives at `My_CFD_Project/03_Results` (default).

### Expected outcomes

- **`case_inventory.csv` / `case_inventory_compact.csv`** under `My_CFD_Project/03_Results/_inventory/` updated to today’s date.
- **36 mesh-qualified campaign cases** visible (plain `Multi_Layer_*` `u0p3_p*M` plus `Sin_ST` / `Sin_SL` `u0p*_p*M__mesh_max100_min006_cpg3_bl3`).
- **`max_iter_target` column = 1000** (not 2000).
- **No `CONVERGED` from artifacts alone** — non-converged preliminary runs at 1000 iterations should show **`convergence_status = MAX_ITER_REACHED`** and **`case_status = NEEDS_SOLVER_RERUN`** (not `CONVERGED`).
- **`likely_complete` may still be true** for some max-iter cases (known F-02b; separate future fix).

### Quick post-inventory checks

```bash
cd /c/PyFluent
wc -l My_CFD_Project/03_Results/_inventory/case_inventory.csv
grep -c 'mesh_max100_min006_cpg3_bl3' My_CFD_Project/03_Results/_inventory/case_inventory.csv
grep ',1000,' My_CFD_Project/03_Results/_inventory/case_inventory.csv | head -3
grep 'READY_FOR_POSTPROCESSING' My_CFD_Project/03_Results/_inventory/case_inventory_summary.txt
grep 'MAX_ITER_REACHED' My_CFD_Project/03_Results/_inventory/case_inventory_summary.txt
```

Note the **`Ready for postprocessing:`** count printed at the end — use it to sanity-check step 5.

---

## 4. F-01 remediation — re-extract reports for 15 mesh-qualified cases

**Duration:** ~1–3 hours (15× Fluent report worker; **not** overnight unless cases are slow)

### Why `06` and not `01_batch_report_extract`?

| Mechanism | Skip behavior | Fixes F-01? |
|-----------|---------------|-------------|
| **`01_batch_report_extract.py`** | `SKIP_EXISTING_REPORTS = True` in `00_batch_post_config.py` skips when `summary_metrics_wide.csv` **passes validation** — wrong `expected_outlet_gauge_pressure` in `pressure_report.csv` does **not** fail that check | **No** (unless you set `SKIP_EXISTING_REPORTS = False` in config) |
| **`06_batch_postprocess_all_cases.py`** | `--force` bypasses report skip (`report_exists && skip_existing && !force` at `:825-827`) and writes a per-case config via **`parse_case_operating_values`** (mesh suffix aware) | **Yes** |

**Correct override:** `06` with **`--force --run-reports --no-run-pyensight-contours --no-run-shear`**.

### `--case-status` — do not hard-code one label

Run **step 3 (inventory) before step 4**. Inventory reclassification can move
cases between `case_status` labels (historically F-02 → `NEEDS_SOLVER_RERUN` at
the 1000-iter cap; **F-02c** → `POSTPROCESSED_UNCONVERGED` for max-iter cases
that already have full basic post artifacts). A single-label
`POSTPROCESSED_BASIC`-only filter can select **0 cases** after re-inventory
(F-21).

Pass a **union** of statuses that covers post-processed cases after a fresh
inventory, for example:

```text
POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING
```

When in doubt, check the row in `case_inventory_compact.csv` before running `06`.

### Skip-existing note

- **`--force`** re-runs **reports only** (contour/shear disabled below).
- Without `--force`, existing `summary_metrics_wide.csv` → `SKIPPED_EXISTING` (no fix).

### Automated loop (recommended — runs only cases that already have `pressure_report.csv`)

Your verification found **15** post-processed coarse-mesh sinusoidal cases. This loop targets exactly those (skips solved-but-never-post-processed folders):

```bash
cd /c/PyFluent
source .venv/Scripts/activate

MESH_SUFFIX='__mesh_max100_min006_cpg3_bl3'
CASE_STATUSES='POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING'
for GEO in Sin_ST Sin_SL; do
  for BASE in u0p1_p4M u0p1_p6M u0p1_p8M u0p2_p4M u0p2_p6M u0p2_p8M u0p3_p4M u0p3_p6M u0p3_p8M; do
    CASE="${BASE}${MESH_SUFFIX}"
    PRESSURE_CSV="My_CFD_Project/03_Results/${GEO}/${CASE}/post/reports/pressure_report.csv"
    if [[ ! -f "$PRESSURE_CSV" ]]; then
      echo "SKIP (no prior post): ${GEO}/${CASE}"
      continue
    fi
    echo "=== F-01 report re-run: ${GEO}/${CASE} ==="
    python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py \
      --geo-name "$GEO" \
      --case-name "$CASE" \
      --case-status "$CASE_STATUSES" \
      --force \
      --run-reports \
      --no-run-pyensight-contours \
      --no-run-shear \
      --continue-on-error
  done
done
```

### Explicit per-case commands (18 coarse-mesh names; expect **15** to run)

If you prefer one line per case, run only the lines whose `pressure_report.csv` exists.

**Sin_ST**

```bash
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p1_p4M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p1_p6M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p1_p8M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p2_p4M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p2_p6M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p2_p8M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p3_p4M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p3_p6M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
cd /c/PyFluent && python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py --geo-name Sin_ST --case-name u0p3_p8M__mesh_max100_min006_cpg3_bl3 --case-status POSTPROCESSED_BASIC,POSTPROCESSED_UNCONVERGED,NEEDS_SOLVER_RERUN,NEEDS_SHEAR_POSTPROCESSING --force --run-reports --no-run-pyensight-contours --no-run-shear
```

**Sin_SL** — same nine `case_name` values with `--geo-name Sin_SL`.

### Verification after step 4

```bash
cd /c/PyFluent
# Expect 4e6 / 6e6 / 8e6 matching u0p*_p4M / p6M / p8M — NOT all 6000000.0
grep -h 'expected_outlet_gauge_pressure' \
  My_CFD_Project/03_Results/Sin_ST/*__mesh_max100_min006_cpg3_bl3/post/reports/pressure_report.csv \
  My_CFD_Project/03_Results/Sin_SL/*__mesh_max100_min006_cpg3_bl3/post/reports/pressure_report.csv \
  2>/dev/null | sort | uniq -c
```

Spot-check one 4 MPa and one 8 MPa case:

```bash
grep expected_outlet_gauge_pressure \
  My_CFD_Project/03_Results/Sin_ST/u0p1_p4M__mesh_max100_min006_cpg3_bl3/post/reports/pressure_report.csv
grep expected_outlet_gauge_pressure \
  My_CFD_Project/03_Results/Sin_ST/u0p1_p8M__mesh_max100_min006_cpg3_bl3/post/reports/pressure_report.csv
```

Expect **`4000000.0`** and **`8000000.0`** respectively.

---

## 5. Post-processing batch (`06`) — 36 campaign cases

**Duration:** **overnight** (36 × report + PyEnSight contours + PyFluent shear; typically tens of minutes per case)

### Case selection

Default `--case-status` is **`READY_FOR_POSTPROCESSING` only**. That **excludes**:

- Already-posted cases (`POSTPROCESSED_BASIC` and, after F-02c,
  `POSTPROCESSED_UNCONVERGED`) — contours already exist; good for step 5 skip
- **`NEEDS_SOLVER_RERUN`** hard-failure / incomplete-artifact cases unless you
  add that status explicitly

For the full **36-case deadline campaign** (including `MAX_ITER_REACHED` preliminary runs you still want contoured), use:

```bash
cd /c/PyFluent
source .venv/Scripts/activate

python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py \
  --case-status READY_FOR_POSTPROCESSING,NEEDS_REPORT_EXTRACTION,NEEDS_SOLVER_RERUN \
  --continue-on-error
```

**Do not pass `--force`** unless you intend to re-export everything. Default `--skip-existing` (on) skips stages whose outputs already exist.

Optional dry-run first:

```bash
python My_CFD_Project/01_Scripts/post_processing/06_batch_postprocess_all_cases.py \
  --case-status READY_FOR_POSTPROCESSING,NEEDS_REPORT_EXTRACTION,NEEDS_SOLVER_RERUN \
  --dry-run
```

### Expected behavior with fixed orchestrator (commit `4d4d85a`)

| Observation | Expected? |
|-------------|-----------|
| `pyensight_contour_stage_status = WARN` on `cp_inlet` | **Yes** — F-20 bulk-center-average path still broken; **not a deploy regression** |
| `pyensight_contour_stage_status = SUCCESS` on `water_flux`, `lmh`, `salt_flux` | Yes |
| `pyensight_contour_stage_status = UNKNOWN` | Only if worker exit 0 but `contour_export_status.json` missing/unreadable (should be rare on server) |
| Stage `FAILED` | Investigate logs under `03_Results/_inventory/batch_postprocess_logs/` |
| Pre-fix “all SUCCESS” masking | **Fixed** — JSON inference should show `WARN` when `summary.warn > 0` |

Results land in:

- `My_CFD_Project/03_Results/_inventory/batch_postprocess/batch_postprocess_results.csv`
- Per-case logs: `My_CFD_Project/03_Results/_inventory/batch_postprocess_logs/`

---

## 6. Verification after step 5

### Inventory spot-check — Sin_ST u0p2 / u0p3 max-iter labels

```bash
cd /c/PyFluent
grep 'Sin_ST' My_CFD_Project/03_Results/_inventory/case_inventory.csv | \
  grep -E 'u0p2_p[468]M__mesh_max100_min006_cpg3_bl3|u0p3_p[468]M__mesh_max100_min006_cpg3_bl3' | \
  cut -d, -f2,3,10,11,12
```

Adjust column numbers if needed (`case_name`, `convergence_status`, `case_status`, `max_iter_target`). Expect **`MAX_ITER_REACHED`** / **`NEEDS_SOLVER_RERUN`** for non-converged preliminary cases — **not `CONVERGED`**.

### Batch results status counts

```bash
cd /c/PyFluent
python - <<'PY'
import csv
from collections import Counter
from pathlib import Path
p = Path("My_CFD_Project/03_Results/_inventory/batch_postprocess/batch_postprocess_results.csv")
if not p.is_file():
    raise SystemExit(f"Missing {p} — run step 5 first")
with p.open(newline="", encoding="utf-8-sig") as fh:
    rows = list(csv.DictReader(fh))
print(f"cases in results: {len(rows)}")
for col in ("report_stage_status", "pyensight_contour_stage_status", "shear_stage_status"):
    c = Counter(r.get(col, "") for r in rows)
    print(f"\n{col}:")
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}")
PY
```

**Expect:** `pyensight_contour_stage_status` includes **`WARN`** (cp_inlet / F-20), **not** all `SUCCESS`. Some `UNKNOWN` possible on shear only for very old PNGs without JSON (your pre-check showed all 45 shear dirs had JSON).

### Re-check F-01 pressures (should still be correct after step 5)

Step 5 without `--force` should not re-skip reports on the 15 fixed cases. Re-run the step 4 `grep` if you used `--force` on a broad batch.

---

## Risk flags and ordering

| Topic | Guidance |
|-------|----------|
| **Order** | **1 → 2 → 3 → 4 → 5 → 6** is correct. Run **step 4 before step 5** so `pressure_report.csv` / report metadata is fixed before any new contour run reads PyFluent CSV for cp_inlet Strategy 0. |
| **Step 4 vs 5 overlap** | Step 4 targets coarse-mesh cases by path + broad `--case-status`; step 5 uses a different status union — avoid `--force` on step 5 unless re-exporting everything. |
| **Step 4 case-status** | After step 3 inventory, do **not** rely on `POSTPROCESSED_BASIC` alone — include `POSTPROCESSED_UNCONVERGED` (F-02c) and see F-21 in `REVIEW.md`. |
| **Max-iter cases** | Posted max-iter cases are `POSTPROCESSED_UNCONVERGED` (usable-but-capped, not a `07` queue target). Including `NEEDS_SOLVER_RERUN` still exports hard-failure / incomplete-artifact fields — **do not** treat either as MFBO-grade converged data. |
| **`--force` on step 5** | **Risky** — re-exports all stages for every selected case (hours of extra EnSight/Fluent). Omit unless debugging. |
| **Re-inventory after post** | After step 5 completes, re-run **step 3** once so `case_inventory` reflects new `post/` artifacts. |
| **F-20** | Universal `cp_inlet` WARN is **known**; fix is post-campaign (see REVIEW.md F-20). |
| **F-02b / F-02c** | `likely_complete` requires `CONVERGED` (F-02b). Posted max-iter → `POSTPROCESSED_UNCONVERGED` with `needs_solver_rerun=False` (F-02c). |

### Long-running steps (plan overnight time)

| Step | Typical duration |
|------|------------------|
| 1–2 | Seconds / &lt;1 min |
| 3 | Minutes |
| **4** | **~1–3 h** (15 Fluent report sessions) |
| **5** | **Overnight** (36 cases × 3 stages; PyEnSight + PyFluent dominate) |
| 6 | Minutes |

---

## Commit reference (WSL → server pull)

Deploy pulls these fixes (among others):

- F-01 mesh-qualified operating parse (`06`)
- F-02 inventory `max_iter` default 1000 + artifact `CONVERGED` removal
- F-03 `03` default fields, `08` partial-export exit, `06` JSON status inference
- Tests + REVIEW verification notes

```bash
git log --oneline origin/main~8..origin/main
```
