# Remediation plan — 279-run sweep 전 작업 목록

이 문서는 실행 단위의 source of truth다. 한 작업만 지시할 때도 이 표를 기준으로
범위를 고정한다. 작업이 끝나면 해당 행의 `status`만 갱신한다.

- 작성 기준: 네 독립 리뷰 (아래 출처). 코드/config/test는 이 문서를 쓴 턴에서 바꾸지 않았다.
- 리뷰 기준 commit: `d453e0a` (Claude / Sol / Cursor). Astra는 더 이른 `4fc26b7`이나
  여기에 적힌 측정 결정과 A-02/A-07/D-01/D-02는 이후 커밋에서도 유효하다.
- `C:/ro_data`와 live Fluent는 이 머신에서 접근 불가. 그 값이 필요한 검증은
  workstation 명령으로 적는다.

## 출처

읽은 순서: `docs/review/claude_review_notes.md`, `sol_review_notes.md`,
`astra_review_notes.md`. 네 번째 패스 산출물 `docs/review/cursor_review_notes.md`는
저장소에 **있다** (없는 줄 알고 요청문에 요약한 항목은 Cursor T4 / T5-04 / T5-05 /
T1-08과 일치한다).

Finding id는 파일마다 독립이다. 같은 `T1-01`이 Sol과 Cursor에서 다른 항목이다.
아래 `source findings`는 `Claude C…`, `Sol T…`, `Cursor T…`, `Astra A/B/C/D-…`로 적는다.

둘 이상이 같은 결함을 가리키면 **established**로 취급한다. Sol의 T4–T6은
progress 기준으로 미완이므로 skip/hygiene/pytest는 Claude + Cursor + Astra로 확정한다.

## 우선순위 — 동의와 불일치

심각도 순위는 요청문의 1–7과 같다. **실행 순서와 심각도는 다르다.**

1. **동의.** stop-reason 실패 + solver skip 강화는 한 작업이다. 분리하면
   RUNNING + `.cas.h5`/`.dat.h5` + exit 0 조합이 영구 skip으로 남는다.
2. **동의.** solver 스테이지 transient retry는 extract(`efbced9` /
   `batch_postprocess_all_cases.classify_retryable_report_failure`) 뒤에 온다.
   다만 retry는 R-02 없이 넣으면 1회차 leftover finals가 2회차를 skip한다. **R-02가 먼저.**
3. **동의.** 31개 mesh는 이미 per-geometry 값으로 존재한다. matrix generator는
   복구가 아니라 279 dict를 `_COMMON_MESH` 복사로 만들 때의 예방이다.
4. **동의, 순서만 고정.** schema/writer가 먼저, 31 leaf backfill이 다음, live raise
   gate가 마지막이다. 이유는 A-03: required field를 backfill보다 먼저 넣으면
   기존 31개가 `read_mesh_manifest`에서 깨진다. 측정값 자체는 31개 mesh log의
   `/mesh/check`에 남아 있어 재meshing은 필요 없다.
5. **동의.** 세 dead gate는 배선 전에 31 mesh에 대한 blast radius를 적는다.
   기존 유효 mesh를 거절하는 gate는 없는 것만 못하다.
6. **동의하되 실행은 맨 앞.** 세 실패는 test 기대값이 stale이고 코드가 옳다
   (Claude C6 / Cursor T6 / Astra D-01). Cursor는 “스위프를 막지 않음”으로 유예했으나
   따르지 않는다. 빨간 suite 위에서 R-02 이후를 검증할 수 없고, 이번 주 결함은
   전부 부분 pytest를 통과한 채 merge됐다.
7. **동의.** 값싼 cross-field 항등식. Sol T3가 “279 전 필수”로 표시한 나머지
   20여 개(NaN config, ML 내부 identity, mesh_id token, CP finite, …)는 이 문서의
   본 작업에 넣지 않는다. 전부 하면 세션이 쪼개지지 않는다. 목록은 맨 아래
   deferred mechanical.

요청문에 없으나 둘 이상이 silent-success로 본 항목: inlet TUI 실패 후
`Inlet BC set` (Cursor T2-21, Sol `solver_code_260616.py:1204`). stop-reason과
같은 계급(완료처럼 보이는 틀린 run)이라 **before sweep**에 넣었다. URF abort는
Claude는 279 전, Cursor는 NEEDS-ASTRA라 **넣지 않는다.**

## Task table

Row order is execution order. This ordering supersedes both the original body and V-02 step 2.

| id | title | why now | source findings | files | depends on | verification | status |
|---|---|---|---|---|---|---|---|
| R-01 | Green full pytest + suite gate | **before sweep** — 이후 모든 검증이 pytest이고, 현재 suite는 red | Claude C6-01 C6-02 C6-03 C6-04 C5-20; Cursor T6-01 T6-02 T6-04 T5-08; Astra D-01 | `tests/test_backfill_run_manifest_fields.py`; `tests/test_inventory_convergence_classification.py`; `docs/AGENTS.md`; `docs/DEPLOY_RUNBOOK.md`; `README.md`; `scripts/run_full_pytest.sh` (신규); `.github/workflows/pytest.yml` (신규) | none | 성공 조건은 고정 숫자가 아니다. 실행 후 측정: WSL 2026-09-11 `1059 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`. workflow는 push/PR에서 같은 pytest 선택을 돌리고 nonzero면 check 실패. merge 차단은 GitHub branch protection required check가 필요하며 이 작업이 설정하지 않는다. | COMPLETE |
| R-07 | Production 279 matrix를 registry에서 생성 | **before sweep** — `_COMMON_MESH` 복사는 Diamond에 `n_active_cells=7`을 찍는다. 31 mesh 수리 아님 | Claude C1-03 C1-13 C3-07 C5-16 C5-17; Cursor T1-06 T1-07 T5-09; Sol T1-03 T3-08; Astra A-02 D-02 | `src/ro/campaign_matrix.py` (신규); `configs/batch_config.py`; `scripts/batch_meshing.py`; `scripts/batch_solver_sweep.py`; `tests/test_campaign_matrix.py`; `docs/PIPELINE_MAP.md`; `docs/DEPLOY_RUNBOOK.md` | R-01 | WSL 2026-09-11 `1070 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). live lists: exploratory `mesh_batch_cases` 22 / `solver_sweep_cases` 5; production mesh 31, production solver 279, geo set == `CAMPAIGN_GEO_IDS`, 각 geo 9 `(u,p)`, `run_id` == `make_base_case_name(u,p)`. 31 mesh의 `n_active_cells`/`cell_length_x_m`/`periodic_shift_y`가 registry와 per-geo 일치 (Diamond는 `_DIAMOND_LAYOUTS`와 동일). distinct `mesh_id` 2: 전부 `max085_min006_cpg5_bl4_peel2` except `D0817_a60` → `max060_min006_cpg5_bl4_peel2` (`m_max==0.060`). `--case-set` default exploratory. | COMPLETE |
| R-08 | mesh manifest에 측정 `domain_extent_*_m` writer (optional field) | **before sweep** — gate를 나중에 달면 재meshing 없이 검증할 값이 없다. required로 올리면 31 leaf가 깨진다 | Claude C1-14 C3-01; Sol T3-10; Cursor T3-01 T3-12; Astra A-04 | `scripts/meshing_code_260616.py` (`build_mesh_manifest_payload`); `src/ro/mesh_common.py` (이미 파싱함, 미변경); `src/ro/manifest.py` **REQUIRED 목록은 건드리지 않음**; tests | R-01 | WSL 2026-09-11 `1075 passed, 1 skipped, 0 failed`. payload가 `parse_mesh_metrics_text`의 `domain_extent_x/y/z_m`를 그대로 보존 (config `cell_length_x_m * n_total` 아님). 파서 miss는 **키 있음 + `None`** (0.0 아님; 로그 파일 없음도 동일). extent 키 없는 raw JSON fixture는 `read_mesh_manifest` 성공. `MESH_MANIFEST_REQUIRED_FIELDS` / `_GEOMETRY_FIELDS` 미변경. | COMPLETE |
| R-09 | 31 leaf backfill + `validate_layout_against_x_extent` live gate | **before sweep** — validator는 구현·테스트만 있고 live caller가 없다. 31 log에 `/mesh/check`가 남아 있다 | Claude C1-15 C3-01 C5-03; Sol T3-10; Cursor T3-01 T5-01; Astra A-04 | `scripts/backfill_mesh_manifest_fields.py` (extent는 registry가 아니라 log에서); `scripts/meshing_code_260616.py` write 직전; `scripts/solver_code_260616.py` preflight; `src/ro/domain_layout.py` (`require_*`가 `.ok`를 보고 raise); tests | R-08 | **Part 1 (WSL 2026-09-11):** `1084 passed, 1 skipped, 0 failed`. **Part 2 (workstation 2026-09-11):** dry-run then `--apply` 31 log addition, 0 registry, 0 NO-MEASUREMENT, 0 fail. **Part 3 (WSL 2026-09-11):** x-only live gate, `rel_tol=1e-5`. `1096 passed, 1 skipped, 0 failed`. **gate check after apply+pull:** 31 pass / 0 fail / 0 skip (`rel < 1e-5` vs `layout.total_length_m`). | COMPLETE |
| R-02 | Stop-reason fail-closed + solver skip 강화 | **before sweep** — 완료처럼 보이고 재실행되지 않는 유일한 경로. 둘은 한 작업 | Claude C2-17 C4-02 C4-09; Cursor T2-20 T4-02; Sol T2 `solver_code:3686-3749`; Astra A-07 C-02 | `scripts/solver_code_260616.py`; `scripts/batch_solver_sweep.py`; `src/ro/solver_common.py` (`STOP_REASON_VALUES`); `tests/test_batch_driver_outcomes.py` 및 solver stop-reason 테스트 | R-01 | WSL 2026-09-11 `1108 passed, 1 skipped, 0 failed`. stop-reason None → `stop_reason_determination_failed` + isolated write only + raise. RUNNING+finals → run. residual_converged+previous files+no cas/dat SHA → run. matching SHA+attempt id+allowed reason+mesh hash → skipped_existing. | COMPLETE |
| R-06 | Inlet TUI 실패를 fatal로 | **before sweep** — 잘못된 inlet이 `Inlet BC set` + exit 0으로 남음 | Cursor T2-21; Sol T2 `solver_code:1204` | `scripts/solver_code_260616.py`; `tests/test_apply_inlet_velocity_boundary.py` | R-02 | WSL 2026-09-11 `1115 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). TUI except / TUI 무예외+불일치 readback / solver None / plug 불일치 → `RuntimeError`, `"Inlet BC set"` 없음. 성공 로그는 `vin` Settings readback이 요청값과 일치한 뒤에만. 워커 exit는 R-02. | COMPLETE |
| R-03 | Solver-stage transient retry | **before sweep** — 가장 긴 세션에 retry가 없고, leftover finals는 R-02 없이는 2회차를 죽인다 | Claude T4 부수; Cursor T4-02; Astra C-02; extract 패턴 `efbced9` / `classify_retryable_report_failure` | `src/ro/session_retry.py` (신규); `scripts/batch_solver_sweep.py`; `scripts/batch_postprocess_all_cases.py`; `scripts/batch_meshing.py` (socket 패턴 import); tests | R-02 | WSL 2026-09-11 `1131 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). socket / Scheme heap / launch-spawn (`LaunchFluentError`, `Deadline Exceeded`, `hwtree`, `Aborting:`) → 최대 3 attempt, 새 subprocess. residual/QoI/UDF/G/inlet readback/manifest → attempts==1. session 서명이 blob 어디에 있어도 wrapper보다 이김. attempt 로그는 `{geo}__{mesh}__{run}__solver_attemptN.log`. leftover RUNNING+files는 skip 아님 (R-02). | COMPLETE |
| R-04 | Mesh skip 강화 + `rebuild_mesh_manifest --from-log` | **before sweep** — `.msh.h5`만 있으면 skip되고 rebuild는 기존 manifest를 요구 | Claude C4-01 C4-07; Cursor T4-01; `docs/PIPELINE_MAP.md` §4.7 | `scripts/batch_meshing.py`; `scripts/rebuild_mesh_manifest.py`; `tests/test_mesh_skip.py`; `tests/test_rebuild_mesh_manifest.py` | R-01 | WSL 2026-09-11 `1144 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). msh만 / empty msh / invalid manifest / SHA mismatch / layout mismatch → skip 아님. msh+valid manifest+`mesh_sha256` 일치+현재 case layout 일치 → `skipped_existing`. `--from-log`는 log+`.msh.h5`+registry+run config로 payload 생성; 기존 manifest·로그 부재·x-extent 실패는 write 없음. `created_utc`/`generator_version`=`unrecoverable-from-log`, `inlet_profile_G`=null. **workstation 2026-09-11:** production 31/31 SKIP (sha+layout match); extra 13 SKIP / 2 RUN (`bl12_peel2`, `bl12_f015_peel2`, ~4.79 MB msh, no manifest, AR 251.9). | COMPLETE |
| R-05 | Extract/post/inventory skip + nonzero exit | **before sweep** — 유효 CSV만 보고 skip하면 재solve 결과가 안 들어가고, 배치는 exit 0 | Claude C2-12 C2-24 C2-26 C2-28 C4-04 C4-05 C4-06 C4-11; Cursor T4-04 T4-05 T2-17 T4-06; Sol T2 batch 4 (`batch_report_extract`, `batch_postprocess` `return 0`) | `src/ro/extract_skip.py` (신규); `scripts/batch_report_extract.py`; `scripts/batch_postprocess_all_cases.py`; `scripts/pyfluent_report_extract.py`; `scripts/case_inventory.py`; `src/ro/manifest.py` (`_iter_child_dirs`); `tests/test_extract_skip.py` | R-03 | WSL 2026-09-11 `1169 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). CSV-only / sidecar 없음 / R-02 hash·attempt 없음 / file 또는 sidecar hash mismatch → skip 아님 (mtime은 보지 않음). matching sidecar+manifest hashes+file bytes → skip. extract/post FAILED 또는 aggregate write 실패 → exit ≠ 0; selected 0건은 0. listing `OSError` → 빈 트리로 성공하지 않음. `--skip-unreadable-manifests`는 계속 스캔하되 skipped leaf가 있으면 inventory exit ≠ 0. **workstation 2026-09-11:** 0 SKIP / 19 RUN (3 no CSV, 13 pre-`lmh_mass_balance_signed_python`, 3 column+no sidecar). 19개는 campaign 아님; 재extract 안 함. | COMPLETE |
| R-11 | Campaign/solver 항등식 gate | **before sweep** — 이미 있는 값, 계산 비용 없음 | Claude C3-04 C3-05 C3-06 C3-07 C3-10 C2-13; Cursor T3-02 T3-03 T3-04 T3-10 T3-11; Sol T3-07 T3-09; Astra A-02 A-05 | `src/ro/manifest_validation.py`; `src/ro/solver_common.py`; `scripts/solver_code_260616.py`; `configs/run_config.py`; `scripts/case_inventory.py`; `scripts/report_campaign_identities.py` (신규); `tests/test_campaign_identities.py` | R-01; `run_id` 검사는 R-07 `make_base_case_name` | WSL 2026-09-11 `1205 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`). 스키마 `[0,1)` 유지 — 0.08은 `read_mesh_manifest` 통과, campaign gate만 exact 0.0. mesh≠run blocked reject. `u_mean_ms * G == u_target_ms` (rel 1e-6; `u_mean_ms` 정의 유지; D2450 `0.1992807169514518 * 1.00360939613 == 0.2`); unfilled G/`u_mean_ms`와 plug는 N/A. `run_id` token == `make_base_case_name(u,p)` (`u0p2_p6M_plug` 허용). SHA mismatch reject; msh 없으면 SHA N/A (preflight skip). malformed `max_iterations`/`residual_target`는 default 없이 raise. **workstation 2026-09-11:** mesh 44 unread=0 / run 19 unread=0, 모든 identity 0 reject. | COMPLETE |
| R-12 | Extract 항등식 gate | **before sweep** — CSV에 이미 나란히 있음 | Claude C3-02 C3-03; Sol T3-20 T3-22 T3-26; Cursor T3-05 T3-06; 요청문 7 | `src/ro/fluent_report_helpers.py`; `scripts/pyfluent_report_extract.py`; `scripts/report_extract_identities.py` (신규); `tests/test_extract_identities.py` | R-01 | WSL 2026-09-11 `1223 passed, 1 skipped, 0 failed`. CSV 키는 `area_mem`/`m_in`/`m_in_with_sources`/`m_in_mass_source`/`pressure_drop_spacer` (V-01; `pp_` report 이름 아님). missing key는 N/A이지 0이 아님. area rel `1e-9` (REF_empty 측정 1.8e-15). flux 3-key exact + compute `abs_tol` 1e-8. spacer dP vs active-cell 합은 내부 plane 상쇄로 대수적 항등식 (rel 1e-6 numerical floor). `LOAD_BEARING_SUMMARY_METRICS` 미변경. **workstation 2026-09-11:** run 19 unread=0 no_csv=3; area/flux_in/flux_out/spacer_dp reject 0. CSV 16: area+spacer PASS; flux 3 PASS / 13 N/A. | COMPLETE |
| R-13 | `Sin_ST` / `GEO_ORDER` leftover | **can wait** — `post_cases=[]`이면 geometries 리스트는 안 쓰이고, figures는 279 solver를 막지 않음 | Cursor T5-04 T5-05 (요청문이 명시한 Cursor-only 항목) | `configs/batch_post_config.py`; `scripts/make_summary_figures.py`; `src/ro/campaign_geo_ids.py` (`CAMPAIGN_GEO_ID_ORDER`); `tests/test_summary_figure_geo_order.py` | none | WSL 2026-09-11 `1228 passed, 1 skipped, 0 failed`. live `geometries`/`GEO_ORDER` == `CAMPAIGN_GEO_ID_ORDER` (31, `REF_empty` first). 두 파일에 archive `Sin_ST`/`Sin_SL`/`Empty`/`Diamond_Spacer`/`Hole_Pillar`/`Multi_Layer_*` 없음. | COMPLETE |
| R-10 | 세 dead gate blast radius 후 배선 | **before sweep** — 구현은 있고 배선만 없다. 기존 31을 거절하면 더 나쁘다 | Claude C5-02 C5-04 C5-05 C3-10; Astra A-05 (hash); Cursor는 layout validator를 T5-01로 이미 R-09에 넣음 | `src/ro/manifest_validation.py`; `src/ro/domain_layout.py`; `src/ro/campaign_geo_ids.py`; solver/extract preflight; `scripts/rebuild_mesh_manifest.py` SHA 로직 재사용; 이 파일 R-10 절에 blast 숫자 기록 | R-01; spacer Fluent 대조는 workstation. hash/legacy path는 `RO_DATA_ROOT` | 아래 R-10 절차. blast가 0 reject일 때만 wire. 하나라도 reject면 배선 중단하고 이 절에 목록을 남긴다. | NOT STARTED |

---

## R-01 — Green full pytest + suite gate

### 변경

세 실패는 전부 기대값이 코드/registry보다 오래됐다. 코드를 맞추지 않는다.

- `tests/test_backfill_run_manifest_fields.py:45,78`: `GEO_ID = "D2450_a45"`이면
  `membrane_blocked_area_frac_geometric`은 registry `None`이다
  (`campaign_geometry.py:297`). 기대값을 `None`으로. `0.0`은 형제 필드
  `membrane_blocked_area_frac`(consumed, campaign policy)와 혼동한 것이다.
- `tests/test_inventory_convergence_classification.py`:
  `len(CASE_INVENTORY_FIELDNAMES) == 120`을 독립 named sequence 계약으로 교체
  (exact set + duplicate 없음 + 열 순서). expected를 production constant에서
  복사하지 않는다. 현재 123열이며 추가분은
  `convergence_quality_warnings`, `pp_pressure_drop_rel_spread_window`,
  `pp_pressure_drop_rel_spread_note`.
- `docs/AGENTS.md`, `docs/DEPLOY_RUNBOOK.md`, `README.md`의
  “784 passed / over 800 / green”을 실제 명령과 **실행 후 측정한** 숫자로 교체.
  Windows symlink skip 숫자는 이 머신에서 재측정하지 말고, WSL 숫자와
  “Windows는 별도 실행”만 적는다. workflow가 merge를 차단한다고 쓰지 않는다.
- `scripts/run_full_pytest.sh`: 로컬 `.venv/bin/python`으로 아래와 같은
  pytest 선택을 실행, nonzero면 실패. CI는 이 스크립트를 호출하지 않는다.
- `.github/workflows/pytest.yml`: 자체 Python setup + pip install 후
  **같은 pytest 선택** (`python -m pytest -q -p no:cacheprovider`).
  Fluent/`RO_DATA_ROOT` 없음. push와 pull request에서 돌고 nonzero면
  check가 실패한다. protected-branch required status check는 GitHub에서
  사용자가 설정한다 — 이 작업의 범위가 아니다.

로컬 명령:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

CSV contract test는 `CASE_INVENTORY_FIELDNAMES`를 복사해 expected를 만들지
않는다. 이름을 독립적으로 나열하고 exact set, duplicate 없음, **sequence**
(CSV 열 순서가 계약)를 검사한다.

### 포함하지 않음

- `RO_DATA_ROOT` skip을 xfail/pass로 바꾸기. skip이 맞다.
- nullable required field의 의미 (`None`으로 존재 vs 없음) — T6 부수, open questions 아님,
  deferred mechanical.
- pre-commit hook, tox, Fluent 통합 테스트.

### 검증

숫자를 성공 조건으로 고정하지 않는다. 실행 후 기록한다.

- 2026-09-11 WSL: `scripts/run_full_pytest.sh` → **1059 passed, 1 skipped, 0 failed**.
  skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
- workflow는 `.venv`를 쓰지 않고 같은 pytest 선택을 설치한 환경에서 돌린다.

---

## R-02 — Stop-reason fail-closed + solver skip 강화

분리 금지. Claude C4-09 / Cursor T4-02 / Astra A-07이 같은 결합이다.
V-03이 본문의 skip 계약을 확장한다: 새 terminal manifest + **이전** final 파일
쌍도 “완료처럼 보이고 재실행되지 않음”이다.

### 현재 계약 (수정 전)

- `determine_and_print_stop_reason` except → warning, `solver_stop_reason is None`
  이면 `finalize_worker_run_manifest` skip, 그래도 `write_case_data`,
  artifact가 있으면 worker가 성공으로 끝날 수 있음.
- `classify_solver_pre_execution`는 `skip_existing_final_data and final_pair_exists`
  만 본다. size/hash/stop_reason/current-attempt 없음.

### 변경

Worker (`solver_code_260616.py`):

1. 새 enum `stop_reason_determination_failed`. `unknown_early_stop`과 섞지 않는다.
2. except/`None`이면 그 값으로 manifest를 **terminal FAILED**로 finalize한다.
   `RUNNING`으로 두지 않는다. parabolic이어도 G를 요구하지 않는다 (V-03).
3. canonical `*_final.cas.h5`/`.dat.h5`는 쓰지 않는다. 세션이 살아 있으면
   `failed_attempt_{solver_attempt_id}.cas.h5`로만 격리 저장을 시도한다.
   그 write 실패는 WARNING이고 원래 determination failure를 가리지 않는다.
   이전 attempt의 canonical 파일은 rename하지 않는다 — skip이 삼키지 못하게
   하는 쪽은 아래 증거이지 파일 삭제다.
4. worker는 `RuntimeError("stop_reason_determination_failed")`로 exit ≠ 0.

Skip (`classify_solver_pre_execution`): 파일 존재만으로 결정하지 않음.

Skip은 다음을 **모두** 만족할 때만.

- `collect_solver_final_artifact_failures`가 빈 리스트 (둘 다 존재, size > 0).
- run manifest가 읽히고 `stop_reason` ∈
  `{residual_converged, qoi_converged, max_iter_reached}`.
  `RUNNING`, `stop_reason_determination_failed`, `diverged`, `not_run`은 skip 아님.
- `mesh_sha256`이 현재 `.msh.h5` bytes와 일치 (`sha256_file`, rebuild와 동일 알고리즘).
- **current-attempt 증거** (아래).

`tests/test_batch_driver_outcomes.py`의 파일-only fixture는 skip이 아니다.

### current-attempt 증거

mtime이 아니다. V-03이 경고한 대로 copy/`touch`가 mtime을 속인다.

증거는 두 조각이다.

1. **`solver_attempt_id`** — `write_worker_run_manifest`가 Fluent iterate **전**에
   새 UUID를 넣는다. 새 attempt가 시작되면 이전 completion hash 필드는 payload를
   새로 쓰면서 사라진다.
2. **`final_case_sha256` / `final_data_sha256`** — canonical `write_case_data`가
   성공하고 파일이 nonempty인 뒤에만 manifest에 찍는다.

Skip은 두 SHA가 **있고** 디스크 상의 그 파일 bytes와 일치할 때만 통과한다.

이전 파일 쌍이 이 증거를 만족할 수 없는 이유: 새 solve는 시작 때 hash 필드를
지운다. stop-reason이 `residual_converged`로 finalize된 뒤 canonical write가
죽으면 hash는 다시 찍히지 않는다. 디스크에 남은 것은 이전 attempt의 bytes다.
manifest에는 그 bytes의 SHA가 없다. 따라서 skip은 거부하고 재실행한다.

해시 없는 기존 완료 leaf(R-02 이전 exploratory)도 skip하지 않는다. fail-closed.
campaign 279는 아직 시작하지 않았다.

### 포함하지 않음

- launch 전 `LAUNCHING` manifest (Claude C4-08 / Astra C-02 orphan leaf). 재실행 가능.
- URF abort, QoI monitor 부재, `batch_solver_rerun` skip CSV.
- extract/mesh skip (R-04, R-05).
- autosave/checkpoint (UNKNOWN).
- attempt-specific temp 디렉터리로 올린 뒤 atomic rename publish 전체 (V-03의
  더 큰 계약). 이번 작업은 hash stamp가 그 구멍만 막는다.
- solver/UDF/template fingerprint를 skip에 넣기 (R-02 행의 mesh hash까지).

### 검증

Fluent 없이 pytest:

- stop-reason None → `stop_reason_determination_failed` finalize, canonical
  `write_case_data` 없음, isolated 경로만, raise → exit ≠ 0.
- isolated write 실패가 determination failure를 가리지 않음.
- `.cas.h5`/`.dat.h5` + `RUNNING` → `run`.
- 같은 파일 + `residual_converged` + **hash 없음** (새 terminal + 이전 파일) → `run`.
- 같은 파일 + 허용 stop_reason + matching cas/dat/mesh hash + attempt id →
  `skipped_existing` (dry-run보다 우선).

---

## R-03 — Solver-stage transient retry

### 변경

`batch_solver_sweep.py`의 `subprocess.run` 한 번을 extract와 같은 루프로 교체.

분류기는 `src/ro/session_retry.py` `classify_retryable_session_crash` 하나.
extract `classify_retryable_report_failure`는 위임. meshing CAD는 그대로 두고
socket 패턴만 공유한다.

Retryable (세션 사망, blob **어디든** 있으면 wrapper보다 이김):

- socket: `IOCP/Socket`, `Connection reset`, `10054`, `forcibly closed`
- Scheme heap: `wta(1st) to string->symbol`, `#[free`, `Attempt to mark a free block`, `Error encountered in critical code section`
- launch/spawn: `LaunchFluentError`, `Deadline Exceeded`, `Failed to construct hwtree`, `Aborting:`

extract 분류기는 세 번째를 커버하지 않았다. R-03에서 같이 넣었다. 바깥
메시지(inlet readback / Canonical CP 등)만 보고 분류하지 않는다.

Non-retryable: residual / QoI 수렴 실패, UDF compile, G marker 부재, R-06
inlet readback, manifest validation. 재시도하면 같은 실패가 난다.

`max_retries=2` → 최대 3 attempt, 매번 **새 프로세스**. 부분 `.dat.h5`에서
resume하지 않는다. skip은 루프 **앞**에서 한 번만. leftover finals 정리는
R-02 skip이 이미 거부한다 (RUNNING+files / hashes 없음 → `run`). retry가
finals를 rename/delete하지 않는다.

로그: `{geo_id}__{mesh_id}__{run_id}__solver_attemptN.log`. worker
`solver_log_*.txt`는 실패 후 `__attemptN`으로 옮겨서 다음 attempt가 덮지
않는다. mesh_id 없는 `{geo}__{run}__report.log` 버그는 재현하지 않는다.

재시도 횟수/kind는 stdout과 run leaf `solver_retry_record.json` (required
schema 아님).

### 고아 Fluent 프로세스

launch가 session handle 전에 죽으면 `solver.exit()`가 없고 fluent.exe가
남을 수 있다. 이 attempt 출력의 고유 `-sifile serverinfo-*.txt`로 command
line을 대조하면 **그 attempt의 프로세스만** 식별할 수 있다. 손으로 연
Fluent는 다른 sifile이라 매칭되지 않는다. **자동 kill은 하지 않는다** —
오탐 한 번이 workstation의 다른 세션을 죽인다. 매칭 PID는 `ORPHAN-PID`로
찍고 수동 종료. sifile이 없거나 process list를 못 읽으면 “프로세스 없음”으로
치지 않고 수동 정리하라고 찍는다.

### 포함하지 않음

- crash 이후 수렴 이력을 이어 풀기. 항상 fresh session.
- 라이선스 실패를 transient로 분류하기 (UNKNOWN, 넣지 않음).
- meshing/extract retry 재설계 (extract 분류기에 launch-spawn만 추가).
- 모든 `fluent.exe` taskkill.

### 검증

WSL 2026-09-11 `1131 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
socket 1회 후 success → attempts==2. launch-spawn 동일. residual/QoI/UDF/G/inlet
readback → attempts==1. leftover RUNNING+files는 skip `run`. attempt 1 로그가
attempt 2에 덮이지 않음. 옛 attempt 로그의 socket 문자열이 현재 UDF 실패를
retryable로 바꾸지 않음.

---

## R-04 — Mesh skip 강화 + `--from-log`

### 변경

Skip (`mesh_skip_block_reason` / `classify_mesh_pre_execution`): `.msh.h5`
존재만으로 skip하지 않는다. skip은 루프 **앞**에서 한 번. skip이 켜져 있는데
증거가 부족하면 `Not skipping existing mesh: …`를 찍고 진행한다.

Skip은 다음을 모두 만족할 때만 (`skip_block is None` → `skipped_existing`,
이유 `complete current-case mesh`):

- `.msh.h5` 존재, size > 0.
- `manifest.json`이 `read_mesh_manifest`로 통과.
- `mesh_sha256` == 파일 bytes (`sha256_file`, rebuild `_sha256_file`과 동일).
- manifest layout 필드가 **현재 case dict** (common_mesh merge 후)와 같다:
  `n_active_cells`, `cell_length_x_m`, `buffer_length_in_m`, `buffer_length_out_m`,
  `periodic_shift_y_m` (config `periodic_shift_y` mm → m).

하나라도 실패하면 remesh 또는 아래 `--from-log`. “파일 있음”은 이유가 아니다.
R-09 x-extent gate가 `write_mesh_manifest` **앞**에서 raise하므로, layout과
어긋난 mesh는 `.msh.h5`만 남고 manifest가 없다. 그 상태는 skip되면 안 된다.

`--from-log` (`rebuild_mesh_manifest.py`): 기존 rebuild는 `manifest.json`이
있는 leaf만 yield하고 기존 payload를 요구한다. 새 모드는 **없는** manifest를
만든다. `--allow-field`와 섞지 않는다. `--mesh-dir PATH`로 한 leaf.

소스는 R-09 backfill처럼 구분해 찍는다:

- **registry** (`geometry_parameters_for_geo_id`): geometry / layout
  (`n_active_cells`, `cell_length_x_m`, `periodic_shift_y` mm 포함).
- **log** (`parse_mesh_metrics_from_log`): quality, `porosity_eps`,
  `domain_extent_*_m`.
- **log** (`parse_meshing_input_summary`): size/BL/label knobs. layout
  periodic shift는 registry가 이긴다.
- **mesh file** (`_sha256_file` of `.msh.h5`): `mesh_sha256`.
- **run config** (`common_mesh_settings`): buffers, `n_lead_excluded`,
  default size knobs.

복구 불가 — 시계/버전/G를 만들지 않는다:

- `created_utc` = `"unrecoverable-from-log"` (validator가 non-empty string을 요구).
- `generator_version` = `"unrecoverable-from-log"`.
- `inlet_profile_G` = `null` (허용된 기본값; solver가 profile load 후 채움).

Refuse (write 없음, 이유 출력):

- mesh log 없음 (extent backfill과 같은 규칙).
- `manifest.json`이 이미 있음 (그건 기존 rebuild + `--allow-field`).
- `.msh.h5` 없음/empty.
- payload가 일반 validator 또는 R-09 x-extent gate 실패.

dry-run 기본, `--apply`로 write. write 전에 live와 같은
`require_layout_matches_measured_x_extent` + `_validate_mesh_payload`.

`S_a193_l1733`: curvature gate는 바꾸지 않았다.
`_CURVATURE_MARGIN_ACKNOWLEDGED_GEO_IDS`에 있어 margin < 1.0이어도 validator는
warn 후 통과한다. `--from-log`도 그 validator를 쓰므로, 오늘 실패한 그 leaf가
지금은 extents/quality/SHA만 맞으면 write될 수 있다. msh-without-manifest는
강화된 skip이 막는다. 두 번째 curvature 검사는 넣지 않았다.

### workstation skip 리포트

WSL은 `C:/ro_data`를 보지 못한다. 31 production leaf + `meshes/` 아래 extra를
한 번에 보려면 Git Bash:

```text
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
python scripts/batch_meshing.py --case-set production --report-skip
```

`--report-skip`은 Fluent를 켜지 않는다. `--case-set`은 무시하고 항상 production
31 + extra leaf. 각 줄: msh 존재/size, manifest ok/missing/invalid, SHA match,
layout match, SKIP/RUN.

**workstation 2026-09-11 (Git Bash, `C:/ro_data`):** production **31/31 SKIP**,
모든 leaf에서 sha=match · layout=match. R-07 generator가 기존 31 manifest와
같다. extra **13 SKIP / 2 RUN**. RUN 두 leaf는
`diamond/D2450_a45/max085_min006_cpg5_bl12_peel2`와
`.../max085_min006_cpg5_bl12_f015_peel2` — AR gate 실패(AR 251.9),
`.msh.h5` 약 4.79 MB, `manifest.json` 없음. msh-only를 skip하지 말라고
R-04를 쓴 바로 그 상태.

### 포함하지 않음

- 31개 재meshing.
- quality-gate 실패 mesh를 자동 삭제.
- `mesh_run_record.json` write 실패를 fatal로 승격 (Claude C2-33, 별건).
- curvature acknowledged set 변경.

### 검증

WSL 2026-09-11 `1144 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
msh만 / empty msh / SHA mismatch / `n_active_cells` layout mismatch → skip 아님.
complete manifest+hash+layout → dry-run보다 `skipped_existing` 우선.
`--from-log` dry-run은 write 없음; `--apply`는 `read_mesh_manifest` 통과.
로그 없음 / 기존 manifest / extents 없는 로그(x-extent gate) → write 없음.
unrecoverable 필드는 marker/null.

다음 행: **R-05**.

---

## R-05 — Extract/post/inventory skip + nonzero exit

### 변경

Skip (`extract_skip_block_reason`): wide CSV 존재 + non-empty columns만으로
skip하지 않는다. V-03이 본문의 mtime 비교를 덮는다. 나중에 복사한 old CSV는
mtime이 새롭고, `copy2`/백업 복원은 old mtime을 보존할 수 있으므로 mtime은
보조도 아니다. 현재 solve의 증거는 R-02가 run manifest에 남긴
`solver_attempt_id` / `final_case_sha256` / `final_data_sha256`이다.
두 번째 hash 메커니즘은 만들지 않았다.

Extract는 성공 완료 시에만 같은 필드 이름의 sidecar
`post/reports/extract_source.json`을 쓴다 (CSV 옆, atomic replace).
이 해시는 extract가 읽은 cas/dat bytes이며, run manifest의 R-02 필드를
extract가 덮어쓰지 않는다. `convergence_quality` manifest write가 실패하면
sidecar를 쓰지 않고 raise한다. 그때 CSV가 디스크에 남아 있어도 sidecar가
없으므로 다음 실행은 skip하지 않는다. load-bearing column guard는
`pyfluent_report_extract.py`에 이미 있고 여기서 재구현하지 않는다.

Skip은 다음을 모두 만족할 때만 (`skip_block is None`):

- `summary_metrics_wide.csv` 존재, size > 0. extract 배치는 여기에
  `validate_summary_wide_csv`를 더한다.
- sidecar가 있고 JSON object로 읽힌다.
- run manifest가 `read_run_manifest`로 통과하고 `solver_attempt_id`와
  cas/dat SHA가 비어 있지 않다.
- 디스크의 final cas/dat SHA가 run manifest와 같다.
- sidecar의 세 필드가 run manifest와 같다.

하나라도 실패하면 재extract. **증거가 없으면 skip이 아니라 재extract.**
기존 ~15 run 중 R-02 이전 leaf는 hash 필드가 없고, sidecar도 없다. 그
상태는 `extract source record was not found` 또는 `missing solver_attempt_id`
/ `missing current-attempt final artifact hashes`로 RUN이다. 한 번 이
코드로 extract가 성공한 뒤에야, R-02 hash가 있는 leaf만 skip할 수 있다.
hash가 없는 leaf는 sidecar를 써도 run manifest 증거가 없어 계속 RUN이다.

`batch_report_extract.py`: `SKIP_EXISTING_REPORTS`가 위 skip을 쓴다.
증거가 부족하면 `Not skipping existing report: …`를 찍고 진행한다.
`--report-skip`은 Fluent 없이 모든 run leaf를 나열한다.

드라이버 exit: `FAILED` / `FAILED_METRIC_VALIDATION` / `MISSING_CASE_DATA`
또는 status·merged CSV write 실패 → `sys.exit(1)`. selected 0건
(`No cases were processed.`)은 실패가 아니다.

`batch_postprocess_all_cases.py`: `--skip-existing`의
`summary_wide.is_file()`와 PNG glob만으로 skip하지 않는다. extract skip이
통과할 때만 report/contour/shear를 `SKIPPED_EXISTING`으로 둔다. 그림 내용
검증 전부는 이 작업이 아니다. `run()`은 FAILED stage가 있으면 1, selected
0건은 0.

`case_inventory.py`: listing `OSError`는 빈 트리로 바꾸지 않는다
(`_iter_child_dirs`가 `Could not list directory {parent}`로 raise, `main`
exit 2). 첫 unreadable manifest는 `{manifest_path}: …`로 이름을 남긴다.
`--skip-unreadable-manifests`는 계속 스캔하지만 skipped leaf가 하나라도
있으면 exit 1 — 빠진 행이 있는 inventory는 쓸 수 없다. `classify_case`는
stale CSV+contours를 `POSTPROCESSED_*`로 두지 않는다
(`extract_is_current`가 있어야 한다). 합성 classify fixture에 그 키가
없으면 예전처럼 `has_summary_metrics_wide`를 current로 본다.

### workstation skip 리포트

WSL은 `C:/ro_data`를 보지 못한다. 기존 run leaf의 CSV / sidecar / R-02
hash / skip 결정을 보려면 Git Bash:

```text
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
python scripts/batch_report_extract.py --report-skip
```

`--report-skip`은 Fluent를 켜지 않는다. 각 줄: csv 존재/size, sidecar
유무, run manifest hash 필드 유무, SKIP/RUN, RUN이면 reason. 기존 15 run은
sidecar가 없거나 (R-02 이전) hash 필드가 없으면 RUN이어야 한다. mtime이
새 CSV처럼 보여도 sidecar가 현재 hash와 다르면 RUN.

### 포함하지 않음

- report-stage retry (이미 `efbced9` + R-03).
- load-bearing column guards (이미 `pyfluent_report_extract.py`; V-01).
- contour/shear 픽셀 검증, PyEnSight except 전수 (Claude T2 미착수).
- `batch_solver_rerun` CSV skip (Claude C4-03, 우선순위 낮음).
- extract가 run manifest의 R-02 hash 필드를 쓰는 것.

### 검증

WSL 2026-09-11 `1169 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
CSV만 / sidecar 없음 / R-02 hash 없음 / cas·dat SHA mismatch / sidecar
attempt mismatch → skip 아님. matching sidecar+hashes는 CSV mtime이
`final_data`보다 오래돼도 skip. extract/post empty selected → exit 0;
FAILED / write 실패 → exit 1. listing `OSError` → empty success 아님.
unreadable manifest는 경로를 찍고, `--skip-unreadable-manifests`면 exit 1.
stale extract + contours → `NEEDS_REPORT_EXTRACTION`, `POSTPROCESSED_*` 아님.

**workstation 2026-09-11:** `batch_report_extract.py --report-skip` →
**0 SKIP / 19 RUN**. 3개는 CSV 없음, 13개는 `lmh_mass_balance_signed_python`
컬럼 이전 CSV, 3개는 컬럼은 있으나 sidecar 없음. 19개 전부 campaign 279가
아니다 (Diamond grid study, REF_empty baseline, 당일 test run). 재extract
하지 않음. sidecar 없는 leaf를 skip하지 않은 것이 R-05 계약.

다음 행: **R-11**.

---

## R-06 — Inlet TUI 실패를 fatal로

### 변경

`solver_code_260616.py` `apply_inlet_velocity_boundary`: Settings/TUI 후보가 모두
실패해도 예전에는 print 후 `Inlet BC set`을 찍고 return했다. 입구는 UDF
velocity profile이 붙는 자리이므로, TUI가 실패하고 run이 계속되면 이전
plug/stale magnitude로 solve되고 로그는 성공처럼 보인다. 기존 gate를 통과하는
물리적으로 틀린 run.

TUI `execute_tui` except는 `RuntimeError`로 다시 올린다. TUI 세션이 없으면
skip-return 하지 않고 raise. 워커 nonzero exit는 R-02가 이미 담당하므로
여기선 raise + readback만.

G marker gate (`agreed_inlet_profile_g`, Claude C1-06)는 **약화하지 않는다.**

### 성공 로그의 증거

`"Inlet BC set"`은 호출이 예외 없이 돌아온 뒤가 아니라, **같은 `vin`
Settings 객체를 다시 읽어 요청값과 비교한 뒤**에만 찍는다. TUI가 예외 없이
반환한 것은 증거가 아니다.

- profile: `velocity_specification_method`가 Components, `velocity_components`
  active, x option `udf`, x UDF 이름 == 요청 `profile_udf_name`, y·z ≈ 0.
- plug: `velocity_magnitude` == 요청 `inlet_velocity` (`math.isclose`).
- 비교 실패면 `RuntimeError` (`Inlet BC readback failed ...`). Settings
  assignment가 예외 없이 끝나도 readback이 틀리면 TUI로 넘기고, TUI 뒤에도
  틀리면 raise.

### 포함하지 않음

- `INLET_G_MAX = 1.02` band 변경 (open questions).
- `u_mean_ms`를 physical bulk로 재정의.
- thread-name 3단 fallback (Cursor T2-22, UDF 의존 NEEDS-ASTRA).
- 워커 exit handling (R-02).

### 검증

WSL 2026-09-11 `1115 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
`tests/test_apply_inlet_velocity_boundary.py`: Settings+TUI 실패, TUI 무예외+stale
readback, solver None, plug 불일치 → raise / `"Inlet BC set"` 없음. Settings
또는 TUI 뒤 matching readback, plug matching magnitude → 성공 로그.

---

## R-07 — Production 279 matrix를 registry에서 생성

### 왜 copy가 위험한가

`configs/batch_config.py` `_COMMON_MESH`는 `n_active_cells=7`,
`cell_length_x_m=0.003465`, `periodic_shift_y=3.465`. Diamond registry
(`campaign_geometry._DIAMOND_LAYOUTS`)는 5–27 cell, pitch 0.943–4.900 mm.
`merge_geometry_into_mesh_manifest`는 그 knobs에 **config wins** (Astra A-02).
`_DIAMOND_MESH_LAYOUTS`는 “not used by this config”이고 `mesh_batch_cases`에
붙지 않는다. 현재 22개(ML+Pillar+Sin+empty)는 7-cell이라 우연히 맞다.

### 변경

순수 generator (`src/ro/campaign_matrix.py` 권장, config 모듈 루프에 숫자를 또
복사하지 않음):

- geo: `CAMPAIGN_GEO_IDS` (31, 이미 assert됨).
- layout/geometry knobs: `geometry_parameters_for_geo_id`. Diamond는 여기서
  `n_active_cells` / `cell_length_x_m` / `periodic_shift_y_m` / angle.
- mesh 공통: `_COMMON_MESH`에서 **layout 키를 빼서** 쓰고, geo별로 registry가
  overlay. `m_max` 예외는 `D0817_a60 == 0.060` 한 줄 (batch_config 주석과
  `_DIAMOND_MESH_LAYOUTS` 마지막 행). 그 외 `0.085`.
- 9 operating points: `u ∈ {0.1, 0.2, 0.3}` × `p ∈ {4e6, 6e6, 8e6}`
  (`docs/DEPLOY_RUNBOOK.md`, `docs/RESTRUCTURE_PLAN.md`). `run_id`는
  `make_base_case_name(u, p)`만. 손편집 문자열 금지.
- assert: unique `(geo_id, mesh_id, run_id)`, `len(production_mesh)==31`,
  `len(production_solver)==279`, 각 geo 정확히 9 run, Diamond n_active가
  `_DIAMOND_LAYOUTS`와 전부 일치.

Entrypoint: exploratory 리스트를 지우지 않는다.

- `mesh_batch_cases` = 현재 22 (exploratory, Diamond 0).
- `solver_sweep_cases` = 현재 5 (REF_empty 2 + D2450 p8M bl4/bl6 + D0817 u0p3 dip).
- 새 이름 `production_mesh_batch_cases` / `production_solver_sweep_cases`.
- `batch_solver_sweep.py` / `batch_meshing.py`에 `--case-set exploratory|production`.
  **기본은 exploratory.** 279를 기본으로 바꾸면 CP-grid/dip 5-case가 사라지고,
  skip 강화 전에 279가 나갈 수 있다. 스위프 당일 production을 명시한다.

`docs/PIPELINE_MAP.md:249`의 “31-mesh campaign matrix” / “two-case starter”를
실제 22/5 vs production 31/279로 고친다. 문서만 고치고 generator가 없으면
C5-16을 반복한다. 그래서 문서 수정은 이 작업에 포함한다.

config vs registry conflict fail-fast (Sol T3-02)는 generator 출력과 registry를
pytest로 비교하는 것으로 충족한다. `merge_geometry_into_mesh_manifest`의
config-wins 자체는 유지한다 (의도된 override 경로). production case dict가
registry와 같으면 덮어써도 값이 같다.

### 포함하지 않음

- 31 mesh 재생성.
- `_COMMON_MESH`에서 7을 지워 exploratory 22를 깨기.
- Pillar registry angle 0 vs config 45 metadata 분쟁의 물리 판정 (Astra A-02).
  production Diamond/ML/Sin/empty layout만 registry에서 주입. Pillar
  `attack_angle_deg`는 현재 exploratory와 같은 명시값을 유지하고, 바꾸려면
  별도 판단.

### 검증

`pytest`가 위 cardinality/Diamond layout/`D0817_a60` m_max/`run_id` identity.
Exploratory lists stay 22 / 5:

```text
python -c "import importlib.util; from pathlib import Path; p=Path('configs/batch_config.py'); s=importlib.util.spec_from_file_location('batch_config', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(len(m.mesh_batch_cases), len(m.solver_sweep_cases), len(m.production_mesh_batch_cases), len(m.production_solver_sweep_cases))"
```

측정 (WSL 2026-09-11): `22 5 31 279`. distinct production `mesh_id` 2
(`max085_min006_cpg5_bl4_peel2`, `D0817_a60`만 `max060_min006_cpg5_bl4_peel2`).
full suite `1070 passed, 1 skipped, 0 failed`.

---

## R-08 — 측정 domain extent를 writer에 (optional)

### 순서 이유 (schema vs backfill)

1. **이 작업:** `build_mesh_manifest_payload`가 이미 파싱하는
   `mesh_metrics["domain_extent_x_m/y_m/z_m"]`를 payload에 넣는다.
   `MESH_MANIFEST_REQUIRED_FIELDS`에 **추가하지 않는다.** 없으면 옛 31개가
   즉시 unreadable (Astra A-03, 34/44 사건과 같은 구조).
2. **R-09:** log에서 31개를 backfill.
3. **R-09 gate:** live meshing write와 solver preflight는 필드가 있고
   `validate_layout_against_x_extent(...).ok`가 아니면 raise.
4. required-field 승격은 31개가 다 채워진 **이후** 선택. 이 계획의 본 작업 아님.

`manifest_validation.py:47-49`는 이미 이 이름을 metre range 대상으로 들고 있다.
writer가 안 채울 뿐이다. `parse_mesh_metrics_text`는 길이를 저장한다
(`x_max-x_min`, mm→m). validator는 `(x_min, x_max)`를 받는다. origin 0이면
`x_min=0`, `x_max=extent`. 현재 CAD origin 0은 보호 결정. `x_min`을 새로
required로 만들지 말고, live gate는 `x_min=0`, `x_max=domain_extent_x_m`으로
호출한다. translated mesh가 나오면 그때 min/max를 저장하는 작업을 연다.

### 포함하지 않음

- `periodic_shift_y_m ≈ domain_extent_y_m` hard equality (Claude C3-08, 0.12%가
  정상일 수 있음, NEEDS-ASTRA).
- schema_version bump.
- 재meshing.

### 검증

`build_mesh_manifest_payload` 단위 테스트: metrics dict의 extent가 payload에 그대로.
extent 키 없는 raw JSON fixture가 계속 load.

측정 (WSL 2026-09-11): full suite `1075 passed, 1 skipped, 0 failed`.
파서: 로그 파일 없음 / `/mesh/check` 블록 없음 / 부분 블록 → 키는 **있고** 값은
**`None`** (0.0이 아니고, 키 누락도 아님). writer는 그 `None`을 JSON `null`로
저장한다. 옛 leaf는 키 자체가 없다. 측정 0은 파서가 0.0을 줄 때만 생긴다.

Quality gate는 manifest write **앞**이다. 실패한 mesh에는 manifest가 없으므로
R-09 backfill 대상은 31 pass leaf다 (R-09 절에 기록).

---

## R-09 — 31 backfill + live layout gate

### 변경

Backfill: `backfill_mesh_manifest_fields.py`는 registry geometry를 넣는다.
extent는 registry에 없다. log 파서 `parse_mesh_metrics_from_log` /
`parse_mesh_metrics_text`를 이 스크립트에 연결하거나, rebuild의 log 경로를
“manifest는 있고 extent만 없음”에 쓴다. **새 세 번째 스크립트를 만들지 않는다.**

Identity (Claude/Sol/Astra가 모두 경고한 naive 식 금지):

```text
buffer_length_in_m + n_active_cells * cell_length_x_m + buffer_length_out_m
    == domain_extent_x_m
```

`DomainLayout.total_length_m`이 좌변이다. `cell_length_x_m * n_total`은
buffer가 pitch의 정수배가 아니면 틀린다 (D0817_a45 buffer_in = 3 pitch,
D2450_a60 buffer_in = 0.707 pitch).

`validate_layout_against_x_extent`는 스스로 raise하지 않는다. caller가
`ok`를 보고 raise. `DEFAULT_EXTENT_REL_TOL = 1e-5`.

호출 위치: meshing manifest write 직전 (새 mesh), solver preflight (기존 31,
backfill 후). extract는 solver가 막으면 충분. 한 곳에만 넣을 거면 meshing
write + solver preflight 둘 다 — meshing만 있으면 이미 구운 31이 안 걸린다.

**Writer vs quality gate (R-08에서 확인):** `apply_mesh_quality_gate`가
`write_mesh_file` / `write_worker_mesh_manifest`보다 먼저다
(`scripts/meshing_code_260616.py`, quality parse → raise on fail → 그 다음에야
mesh file과 manifest). gate 실패는 except로 나가므로 실패한 mesh에는
`manifest.json`이 없다. R-09 backfill 대상은 시도한 mesh 전체가 아니라
quality-pass한 **31 leaf**다.

Workstations에서 31 log에 `/mesh/check`가 없다는 leaf가 나오면 그 geo_id를
이 절에 적고 remesh하지 말고 멈춘다.

### 포함하지 않음

- evaluation window / `n_lead_excluded` 변경.
- y/z extent를 layout과 강제 일치.

### 검증

pytest fixture transcript. workstation:

```text
# dry-run: 31 mesh leaf에 domain_extent_x_m 추가 diff만
# 실제 명령은 구현 시 backfill CLI에 맞춘다. RO_DATA_ROOT 필요.
```

mismatch 1건이면 apply 하지 않는다.

**Part 1 (WSL, 2026-09-11):** `scripts/backfill_mesh_manifest_fields.py`가
registry 채움과 log 측정 extent를 분리한다. 단위 테스트 + full suite
`1084 passed, 1 skipped, 0 failed`. live gate는 아직 배선하지 않음.

**Part 2 (workstation, 2026-09-11):** dry-run과 `--apply` 모두 31/31 leaf가
파싱 가능한 `/mesh/check`를 갖고, 전부 log addition. registry addition 0,
NO-MEASUREMENT 0, failure 0. apply 요약:
`31 leaf(s), 0 registry, 31 log addition(s), 0 NO-MEASUREMENT, 31 usable, 0 failure(s)`.
`S_a193_l1733`은 기존 curvature_margin UserWarning만 냈고 측정은 됨. apply는
게이트 커밋 `ca1e895` pull **전**(`cdf6277`)에 돌렸다.

x identity는 `layout.total_length_m` (= `buffer_in + n_active * pitch +
buffer_out`)이다. naive `cell_length_x_m * n_total`은 Diamond에서 틀림
(예: D0817_a45 naive 0.02772 vs measured/layout 0.03465; D2450_a60 naive
0.03920 vs 0.03490). D2450_a45만 우연히 같다 (buffer가 1 pitch / 2 pitch).
측정 x vs layout은 a30에서 최대 ~6e-9 m (rel ~1.6e-7), 나머지는 float 잡음.

y는 ML/Pillar/Sin/empty가 대체로 0.003465 m. `M_c160`은 0.003465715
(~0.7 µm, rel ~2e-4). Diamond y는 각도별 periodic dy를 따르고 CAD 잔차
있음 (D2450_a45 y 0.003469131 vs 3.465 mm, ~0.12%). z는 다수가 0.00077 m,
Sin ~0.00077058–0.00077073 (~0.09%), D0817_a45 0.00077145 (~0.19%).
y/z를 `1e-5`로 묶으면 기존 유효 mesh를 거절한다. **gate는 x only.**

**Part 3 (WSL, 2026-09-11):** `DEFAULT_EXTENT_REL_TOL = 1e-5` 유지. 관측 잡음
대비 ~60배, 한 셀(~수 %–10%) 대비 세 자릿수 아래.
`require_layout_matches_measured_x_extent`가 missing/null/`not ok`에서 raise.
meshing은 `write_worker_mesh_manifest`가 `write_mesh_manifest` 직전; solver는
Fluent launch 전 preflight. full suite `1096 passed, 1 skipped, 0 failed`.
apply 후 `ca1e895` pull + 같은 날 x-gate 확인: **pass 31 / fail 0 / skip 0**
(`rel < 1e-5` vs `buffer_in + n_active * pitch + buffer_out`). missing/null
skip 없음. apply 없이 solver preflight는 키 없음으로 31개 전부 거절한다.

---

## R-10 — Dead gates: blast radius 후 배선

세 함수는 test-only. 각각 **먼저 31에 대해 실행하고 숫자를 이 절에 적은 다음**
wire한다. reject > 0이면 배선하지 않는다.

### 1. `assert_no_legacy_ml_geo_paths`

- 하는 일: `RO_DATA_ROOT/{geometries,meshes,runs}/ml/M_r*` 존재 시 raise.
- blast: `RO_DATA_ROOT`가 있는 머신에서
  `pytest tests/test_campaign_geo_ids.py::test_production_data_root_has_no_legacy_ml_paths -q`
  (지금은 `RO_DATA_ROOT` 없으면 skip).
- wire: `batch_meshing.py` / `batch_solver_sweep.py` 시작 시 1회.
  data root 없으면 지금처럼 skip이 아니라 production `--case-set production`일 때만
  require env.

### 2. `assert_replace_log_matches_mesh_manifest`

- 하는 일: replace log의 마지막 `.msh.h5` parent == manifest `mesh_id`.
  **로그가 없으면 return** (현재 계약). Sol T3-17은 completed run에 로그를
  필수로 하자고 하나, 기존 유효 run을 거절할 수 있어 **이번 배선은
  “로그가 있으면 일치”만**. 로그 필수화는 deferred.
- blast: 기존 run leaf 개수, 로그 있는 수, mismatch 수. WSL에 `C:/ro_data`가
  없으면 workstation 명령으로 세어 이 절에 적는다.
- wire: solver preflight와 extract preflight. mismatch만 fatal.

### 3. `validate_spacer_wall_zones`

- 하는 일: manifest `spacer_wall_zones` vs Fluent zone 이름의 exact coverage.
  split suffix (ML/Pillar)가 Diamond `wall_spacer`보다 위험 (Claude C5-02).
- blast **Fluent 없이** 할 수 있는 것: 31 mesh manifest의 `spacer_wall_zones` vs
  `geometry_parameters_for_geo_id(...)["spacer_wall_zones"]`. 불일치 목록.
  Fluent 실측 zone은 각 mesh를 열어야 하므로 이 작업의 wire는 solver
  preflight의 live zone list에만 연결한다. 31을 지금 Fluent로 열지 않는다.
- registry와 manifest가 이미 다르면 **배선 금지**, 목록만 남긴다.

### 4. mesh SHA (C3-10 / A-05)

요청문 5의 세 gate에 더해, 리뷰가 같은 값싼 배선으로 본 SHA 실측.
R-02/R-04 skip에 이미 넣었으면 여기서는 solver/extract **시작 시** 한 번 더
호출해 skip을 끈 직접 worker 경로를 막는다. 로직 복제 금지.

### 포함하지 않음

- `validate_layout_against_x_extent` (R-09).
- replace-log 부재를 completed-run 실패로.
- UDF hardcoded membrane names vs config (Astra A-02 표, 별건).

### 검증

blast 숫자 3줄이 이 절에 기록됨. 그 다음 pytest: preflight가 mismatch fixture에서
raise, match fixture에서 pass.

---

## R-11 — Campaign/solver 항등식

스키마 일반 `[0,1)`를 campaign `== 0.0`으로 바꾸지 않는다 (Claude C3-04).
`_GEOMETRY_FIELDS` / `MESH_MANIFEST_REQUIRED_FIELDS`에 필드를 추가하지 않는다 —
그게 `e493975`가 44 leaf 중 34에서 `read_mesh_manifest`를 막은 경로다.
이미 있는 필드 위의 validator다.

| 항등식 | 위치 | 허용오차 / N/A |
|---|---|---|
| `membrane_blocked_area_frac == 0.0` | mesh+run campaign gate (`write_worker`, solver preflight) | exact 0.0. `[0,1)` 안의 0.08/0.06/0.05는 스키마 pass, campaign reject (LMH +8.7/+6.4/+5.3%). 키 없음 → N/A |
| mesh vs run 동명 필드 | solver preflight / `write_worker` | exact `!=`. solver LMH는 mesh manifest, run 경로는 registry. 한쪽 키 없음 → N/A |
| `u_mean_ms * inlet_profile_G == u_target_ms` | `apply_parsed_inlet_profile_g` / finalize / preflight | rel `1e-6`. `u_mean_ms`는 legacy profile coefficient (재정의 금지). D2450_a45: `0.1992807169514518 * 1.00360939613 == 0.2`. plug / unfilled `u_mean_ms` / unfilled G → N/A |
| `run_id` ↔ `(u,p)` | `write_worker`, preflight, `validate_for_solver` | R-07과 같은 `make_base_case_name`. token은 앞 두 `_` 조각 (`u0p2_p6M_plug` → `u0p2_p6M`) |
| `mesh_sha256` ↔ `.msh.h5` | solver preflight (msh가 있을 때만) | exact hex. msh 없으면 SHA skip (restart / 파일 없는 historical). 리포트도 그때 N/A |
| `max_iterations` / `residual_target` 파싱 | `solver_common.py`; inventory·residual report | 실패 시 default 대체 금지, raise (Claude C2-13). `batch_config.py` **파일 로드 실패**만 2000/1e-7 fallback |

### 기존 artifact blast (R-10과 같은 규칙)

유효한 역사 작업을 거절하는 gate는 없는 것만 못하다. pre-R-02 run은 이후
필드가 없다. 그래서 unfilled G/`u_mean_ms`와 msh 없는 SHA는 **reject가 아니라
N/A**다. 0.08을 스키마에서 못 읽게 만들면 campaign gate가 이름을 남기기도
전에 `read_mesh_manifest`가 죽는다.

WSL은 `C:/ro_data`를 보지 못한다. 15 run / 44 mesh leaf의 실제 REJECT 목록을
여기서 숫자로 만들지 않는다. 워크스테이션:

```text
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
python scripts/report_campaign_identities.py
```

Fluent 없음. `RO_DATA_ROOT` 미설정은 raise. 각 leaf에 PASS/REJECT/N/A.
campaign-valid leaf가 REJECT면 그 목록을 이 절에 남기고 gate를 되돌린다
(R-10 규칙). R-04가 production 31 mesh의 sha+layout match를 이미 확인했다.

**workstation 2026-09-11** (`e3d68fc`, Git Bash, `C:/ro_data`):
`Summary: mesh 44 unread=0 (blocked_reject=0 sha_reject=0); run 19 unread=0
(blocked_reject=0 agree_reject=0 run_id_reject=0 sha_reject=0 uG_reject=0).`
44 mesh 전부 `blocked=PASS sha=PASS` (production 31 + extra; R-04 RUN이던
msh-only `bl12_peel2` / `bl12_f015_peel2`는 manifest가 없어 이 스캔에 없음).
19 run 전부 `blocked=agree=run_id=sha=uG=PASS` (letter suffix
`u0p1_p6M_conv2000` 포함). `S_a193_l1733` curvature_margin warn은
`read_mesh_manifest` 기존 동작이고 identity reject가 아니다. 유효 역사
작업을 거절하지 않음.

### 포함하지 않음

- extract SHA wire (helper는 있음; load-bearing은 solver preflight).
- `u_mean_ms` 정의 변경.
- 스키마 `[0,1)` 축소, required field 추가.
- `pp_m_in ≈ rho*u*A` (Claude C3-09, NEEDS-ASTRA).
- `periodic_shift_y` vs measured y (C3-08).
- `cp_canon` 재구성 (Cursor T3-16).
- Sol T3-01 finite helpers, T3-13 ML identities, T3-18 int coercion — deferred.

### 검증

WSL 2026-09-11 `1205 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
blocked 0.08/0.06/0.05 reject, 0.0 pass, 키 없음 N/A. mesh≠run reject.
`u_mean*G` mismatch reject, D2450 fixture pass, plug/unfilled N/A.
`run_id` mismatch reject, `u0p2_p6M_plug`+(0.2, 6e6) pass.
SHA mismatch reject, 파일 없음 reason 있음. malformed `max_iterations` /
`residual_target` (bool, string, missing, non-dict) raise.
0.08은 여전히 `read_mesh_manifest` 통과.
**workstation blast:** mesh 44 / run 19, unread=0, 모든 identity 0 reject.

다음 행: **R-12**.

---

## R-12 — Extract 항등식

모델: `assert_midplane_c_b_matches_boundary_mixing_cup`처럼 rel_tol + raise.
CSV 키는 V-01: `area_mem`, `pp_udm_area_sum`, `m_in`, `m_in_with_sources`,
`m_in_mass_source` (outlet 동일), `pressure_drop_spacer`,
`pp_pressure_drop_cell_N`. report/computed 이름 `pp_area_mem`/`pp_m_in`을
wide record에 쓰지 않는다. missing key를 0으로 채우지 않는다.
`LOAD_BEARING_SUMMARY_METRICS` / 스키마 required에 추가하지 않는다.

| 항등식 | 위치 | 허용오차 / N/A |
|---|---|---|
| `area_mem` vs `pp_udm_area_sum` | extract CSV write 뒤 | rel `1e-9`. REF_empty u0p2_p6M: `1.680871416727065e-4` vs `1.680871416727068e-4` (1.8e-15). 키 없음/blank → N/A |
| `pressure_drop_spacer` vs Σ `pp_pressure_drop_cell_N` | 같은 위치. N = **active cells** (window 아님) | rel `1e-6`, abs `1e-4` Pa (numerical floor). 내부 cell-boundary plane이 상쇄되어 iso-surface ΔP와 per-cell 합은 대수적 항등식. 구현 당시 이산화 갭으로 가정했으나 workstation 16 leaf가 rel 1e-6 안 (u0p3_p6M per-cell 15% spread 포함). 셀 열 하나라도 없으면 N/A |
| `m_in_with_sources == m_in + m_in_mass_source` (outlet 동일) | 같은 위치. compute 경로는 기존 raise | `m_in` = `(without-sources)` 물리값 (뒤집지 않음). 세 키 중 하나라도 없으면 N/A (0 아님). abs_tol = compute `1e-8` kg/s. REF_empty: `5.315285545536234e-4 == 5.326494756117325e-4 + (-1.120921058109091e-6)` exact |

### 기존 artifact blast (R-10/R-11과 같은 규칙)

유효한 역사 CSV를 거절하는 gate는 없는 것만 못하다.

**workstation 2026-09-11** `python scripts/report_extract_identities.py`:
run 19, unread 0, no_csv 3, area_reject 0, flux_in_reject 0,
flux_out_reject 0, spacer_dp_reject 0. CSV 있는 16 leaf는 area와 spacer
dP PASS. flux는 3 PASS / 13 N/A (`lmh_mass_balance_signed_python` 이전,
분해 컬럼 없음).

spacer dP는 16 leaf 모두 rel 1e-6 안. u0p3_p6M도 포함 (per-cell 값이 15%
퍼짐). iso-surface ΔP와 active-cell 합은 여기서 대수적 항등식이다 — 내부
cell-boundary plane이 상쇄. 계획 문구는 이산화 갭으로 적었으나 측정이
뒤집음. 허용오차 rel 1e-6 / abs 1e-4 Pa는 numerical floor로 유지.

live extract는 새 CSV에만 raise하고, 없는 키는 N/A라 옛 CSV를 재읽어도
flux 없는 leaf를 거절하지 않는다.

```text
cd /c/pyfluent
source .venv/Scripts/activate
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export RO_DATA_ROOT='C:/ro_data'
export PYFLUENT_PROJECT_ROOT='C:/pyfluent'
unset PYFLUENT_RUN_CONFIG PYFLUENT_SKIP_VALIDATION
python scripts/report_extract_identities.py
```

### 포함하지 않음

- load-bearing blank column live 연결 (Claude C2-09) — 이미 live. 항등식 3개만.
- flux 컬럼을 required schema / `LOAD_BEARING_SUMMARY_METRICS`에 추가.
- `lmh_mass_balance` vs `lmh_udm_avg` 허용차 변경 (이미 `1e-3` gate).
- z-normal plane 중복 제거 (Claude C5-23).
- `m_in_mass_source == m_out_mass_source` (관측된 부수 항등식, 이 행의 3개가 아님).

### 검증

WSL 2026-09-11 `1223 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
skip은 `RO_DATA_ROOT` 없는 `tests/test_campaign_geo_ids.py`.
REF_empty area 1e-15 pass, 0.1% mismatch reject, 키 없음 N/A.
flux 3-key exact pass, `m_in_mass_source` 없음 N/A (0으로 숨기지 않음),
mismatch reject. spacer exact/floor-내 pass, 큰 불일치 reject, 셀 열 없음 N/A.
`m_in_mass_source`는 load-bearing required가 아님.
**workstation 2026-09-11:** run 19 unread=0 no_csv=3, identity reject 0.
area+spacer 16 PASS; flux 3 PASS / 13 N/A. spacer는 16 leaf rel 1e-6
(대수적 항등식).

다음 행: **R-13**.

---

## R-13 — `Sin_ST` / `GEO_ORDER`

Cursor-only live leftover. Claude는 `geo_id == "empty"` live 잔존을 찾지 못했고
(`family == "empty"`는 정상), Sol T5는 미착수.

- `configs/batch_post_config.py` `geometries`: `CAMPAIGN_GEO_ID_ORDER` (31).
  `post_cases=[]`이면 `cases_from_run_manifests()`라 미사용. product
  generator를 켜도 archive id로 가지 않음.
- `scripts/make_summary_figures.py` `GEO_ORDER`: 같은 31 `geo_id`.
  `geo_name` 열은 extract/manifest의 `geo_id`라 family 이름이 아님.
- `CAMPAIGN_GEO_ID_ORDER`: `REF_empty` 다음 diamond → ml → pillar → sin
  (registry 블록). frozenset `CAMPAIGN_GEO_IDS`는 membership only.

### 포함하지 않음

- `_tmp_*` 하드코딩 (campaign path 아님).
- `docs/archive/` 의 `geo_name == "empty"`.
- tests/fixtures의 archive `Sin_ST` (live leftover가 아님).

### 검증

WSL 2026-09-11 `1228 passed, 1 skipped, 0 failed` (`scripts/run_full_pytest.sh`).
두 live 파일에 archive 이름 없음. `geometries`/`GEO_ORDER` == 31 campaign
ids, `REF_empty` first. extras는 `GEO_ORDER` 뒤에 append.

다음 행: **R-10**.

---

## Protected decisions

뒤집지 않는다. 반론은 해당 측정과 적용 범위를 직접 다루는 새 증거가 필요하다.

| 결정 | 증거 (한 줄) |
|---|---|
| `membrane_blocked_area_frac = 0.0` (consumed / LMH 분모) | wetted-area 캠페인 정책. 0이 아니면 family마다 LMH 분모가 달라지고 D2450_a45 검증 참조가 깨짐 (`campaign_geometry.py` `_MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED`, Astra 존중 목록). geometric footprint는 별 필드이며 Diamond는 `None`. |
| flux 물리값은 `(without-sources)` | `docs/POSTPROCESSING_MAP.md`: bare = without-sources + mass_source, `m_in`/`m_out`은 without-sources. engine/LMH가 이 키를 씀. |
| `periodic_after_surface_mesh = True` | 현재 mandatory. `docs/RESTRUCTURE_PLAN.md`의 False는 역사 기록. |
| extract launch = meshing → `switch_to_solver()` | `4fc26b7`: 이 host에서 direct solver는 `Failed to construct hwtree`. |
| `u_mean_ms = u_target / G` | legacy coefficient, physical bulk velocity 아님 (`manifest.py` docstring, `docs/metrics_conventions.md`). 검사는 하되 정의를 바꾸지 않음. |
| `n_lead_excluded = 3` | 측정된 evaluation window. Diamond에서 lead 3 cell의 물리적 길이가 5.2배 달라지는 것은 알려진 사실이며 이 계획에서 바꾸지 않음. |
| 측정된 inlet G 계약 / campaign G 상수를 지금 바꾸지 않음 | empty와 D2450이 3.2e-6로 일치 (Astra B-09/A-10). band `INLET_G_MAX=1.02`는 open question. |
| Diamond `wall_spacer` 단일 라벨 | cross-family는 total, split은 within-family. Diamond 재meshing은 검증 참조를 무효화 (`batch_config.py` Diamond 주석). |
| origin 0 / centered-z 현재 계약 | 측정 origin gate는 추가할 수 있으나 origin을 재설정하지 않음. |
| Cell 8 mass-closure silent success는 **이미 수정됨** | `LOAD_BEARING_REPORT_NAMES` / flux compute re-raise (Claude C2-01, Cursor T2-01). 되풀이 “수정” 금지. |
| `agreed_inlet_profile_g` fail-closed | marker 없으면 30 s 후 `RuntimeError` (Claude C1-06). G fallback 침묵 통과를 열지 않음. |

---

## Open questions (이 계획에서 고치지 않음)

판단 패스가 결정할 것. 코드 패치를 여기 적지 않는다.

1. **UDF `INLET_G_MAX = 1.02`.** D2450 측정 excess(+0.308%…+0.3742%)의 약 5배.
   벗어나면 `G=1.0` fallback + warning이고 성공 marker가 안 나와 C1-06이 run을
   세운다. `D0817_a60`만 `m_max=0.060`이라 inlet tessellation이 다르다.
   Diamond 전 범위를 이 band가 덮는지, geometry별 band가 필요한지.
2. **30-cell layout vs Fluent report/surface 한도.** extract는
   `range(n_unit_cells+1)`로 동적 생성 (crash하는 Python index는 세 패스 모두
   못 찾음). 정의 수 ~215, Astra C-05의 135 definitions = 736 s를 선형 외삽하면
   ~1170 s. 실제 한도/시간은 live Fluent (Claude C1-08, Sol T1-07, Cursor T1-08).
3. **CP discriminability와 near-wall mesh.** 0.6% between-geometry vs grid
   dependence, Sc ~600–700, 4 prism / 첫 cell ~6.2 µm (Astra B-04, B-08).
4. **empty-channel dP vs analytic plane Poiseuille.** u=0.2에서 2.6%, u=0.3에서
   3.9% excess (Astra B-06). solver 없이 원인 단정 금지.
5. **Diamond u=0.3 converged dP dip.** cells 4/5의 7.8%/15.0% dip, 765 iter
   지속 (Astra B-07). residual 수렴 ≠ time-stable ranking.

---

## Deferred mechanical (판단이 아니라 나중 세션)

본 표에 안 넣은 확정 결함. 279 solver 숫자 생성 경로를 직접 막지는 않는다고
본 패스가 분류한 것.

- URF 적용 실패 abort vs WARN (Claude C2-18 vs Cursor T2-25 NEEDS-ASTRA).
- `batch_solver_rerun` 복제 URF 스택 / `--results-root` 의미 반대.
- z-normal plane 이중화 (Claude C5-23), `list_named_object_names` 4중 복제.
- live extract에 `load_bearing_summary_missing_columns` 연결 (C2-09 / C5-08).
- convergence_quality manifest write 실패 (C2-04).
- leftover Fluent process listing 실패 시 보수적 대기 (C2-37).
- midplane z 측정 실패 → `fallback_z_m=0.0` (Sol T2 helpers:242).
- residual `latest.update` mixed-iteration (Sol T2 solver_common:394).
- Sol T3의 finite/NaN config, ML 내부 identity, mesh_id `_fNNN`, ramp vs
  max_iter inequality, CP finite/set, mass-fraction round-trip.
- `PRESSURE_DROP_CELLS=(4,5,6,7)` continuity 열 이름 정리 (crash 아님).
- `CURRENT_LAYOUT` 이름, `_tmp_*` / `_probe_*`.
- replace-log 부재를 completed-run 실패로 승격 (Sol T3-17).
- figure window 교집합 (Claude C1-16), `manual_view_bounds` (Sol T1-12).
- `PIPELINE_MAP.md` §4.1 `upgrade_*_in_place` dead 오기재 (C5-01) — R-07 문서
  수정 때 같이 해도 되지만 필수 경로 아님.

그림/contour를 논문 비교에 쓰기 전에 별도 pass가 필요하다는 점은 Claude T2
말미 · Astra A-02 표와 같다. 그 pass는 이 문서의 후속이 아니다.


---

## 판단 판정 — 실행 전 보완 조건 (2026-09-11)

**판정: 조건부 승인.** R-01을 먼저 하고 R-02 뒤에 R-03을 두며, extent를 optional writer → measured backfill → live raise로 도입하는 방향은 맞다. 그러나 현재 문구 그대로 실행하면 stale artifact의 재인증, 잘못된 dead gate의 배선, quality FAIL 결과의 채택이 남는다. 아래 판정이 앞의 우선순위·검증 문구와 충돌하면 **이 절을 우선한다**. 기존 task status는 변경하지 않았다.

근거는 요청한 다섯 review 문서다. 문서가 충돌하거나 배선의 정확성이 걸린 구간만 source로 확인했다: `fluent_report_helpers.py:1024-1031,1135-1212`, `pyfluent_report_extract.py:2431-2460`, `convergence_quality.py:41-56,95-220`, `solver_code_260616.py:2162-2593,3518-3552,3660-3758`, `manifest_validation.py:269-353`, `domain_layout.py:410-467,501-532`, `campaign_geo_ids.py:83-108`. codebase 전수 재검토나 pytest 재실행은 하지 않았다. `C:/ro_data`의 31 manifests/logs, actual Fluent state, 실행 성공률은 모두 **UNKNOWN — 다른 머신의 data root/live Fluent가 필요**하다.

### V-01 — 먼저 바로잡을 사실

| 계획의 문구 | 판정 |
|---|---|
| R-01 수정 뒤 `1056 passed, 1 skipped, 0 failed` | 기존 `1056 passed + 3 failed + 1 skipped`에서 세 test를 삭제하지 않고 고치면 baseline 예상은 **1059 passed, 1 skipped**이다. 추가 tests가 있으면 더 증가한다. 실제 수치는 실행 후 기록하며 숫자를 성공 조건으로 고정하지 않는다. |
| 빨간 suite이면 이후 검증이 전부 무의미 | 과장이다. 알려진 세 실패와 새 실패의 차이를 추적할 수는 있다. 그래도 세 expectation 수정이 작고 의미가 명확하므로 R-01을 먼저 끝내는 것이 가장 싸다. `None`인 geometric footprint를 consumed `0.0`으로 바꾸지 않는다. |
| R-12/deferred의 live blank-column gate 미배선 (Claude C2-09/C5-08) | **현재 source와 불일치.** `pyfluent_report_extract.py:2434` → `require_load_bearing_summary_columns` → `load_bearing_summary_missing_columns`가 이미 live다. 새 배선 작업은 삭제 대상으로 취급하고 기존 gate의 통합 회귀검증만 한다. |
| Sol T3-20의 flux decomposition identity 부재 | **현재 source와 불일치.** `fluent_report_helpers.py:1195-1206`에 이미 raise가 있다. 남은 CSV 검사는 serialization/mapping 검증이며 새로운 독립 물리 검증이 아니다. |
| R-12 CSV key가 `pp_m_in`, `pp_m_in_with_sources`, `pp_area_mem` | 이들은 report/computed 이름이다. review에 기록된 wide CSV 이름은 `m_in`, `m_in_with_sources`, `m_in_mass_source`, `area_mem`, `pp_udm_area_sum`이다. outlet도 동일 원칙. 구현 시 어느 record를 받는 validator인지 고정하고 missing key를 default `0`으로 채우지 않는다. |
| LMH/mass closure의 `1e-3` gate가 있으므로 자동 채택도 보호됨 | `evaluate_convergence_quality`는 `PASS/FAIL/UNKNOWN`을 **반환**한다. extract 말미는 이를 manifest에 쓰려다 실패해도 warning으로 끝난다. classifier 존재와 publication/merge의 rejection은 별개다. Sol T3-21의 “차이만 기록”도 classifier 존재를 빠뜨렸지만, 계획 역시 실행 성공과 결과 채택을 분리해야 한다. |
| 둘 이상이 같은 finding이면 established | 우선순위 근거는 되지만 확정 기준은 아니다. 동일 framing/같은 wrapper 누락이 반복될 수 있다. 위 두 gate 사례처럼 caller 연결과 반례로 확정한다. |

R-01의 CSV contract test는 production constant에서 expected set를 그대로 복사해 비교하면 자가검증이 된다. 독립적으로 명시한 expected names에 duplicate 방지까지 검사하고, 순서가 계약이면 sequence도 검사한다. CI는 clean checkout에서 Python/dependencies/`.venv`를 준비하는 단계가 필요하다. 기존 local `.venv/bin/python`만 호출하는 workflow는 새 runner에서 실행되지 않는다. protected-branch required check까지 설정하지 않으면 workflow 추가만으로 merge를 강제 차단한다고 표현하지 않는다.

### V-02 — 권장 실행 순서

Task table 행 순서가 이 절(특히 step 2)과 본문의 원래 서술을 대체한다.

1. **R-01:** stale expectations 수정, 재현 가능한 full-suite 명령 확보. 각 후속 작업도 full suite와 해당 fault-injection tests를 실행한다.
2. **R-07 → R-08 → R-09:** 사용할 **31개의 exact mesh leaf**와 279 operating-point set을 먼저 고정한다. optional extent writer/backfill을 만든 뒤 workstation에서 선택된 31개를 확인한다. R-11의 finite/type/identity primitives와 R-10의 관찰 도구는 이때 함께 준비해도 된다. workstation 검증을 마지막까지 미루지 않는다.
3. **보강한 R-02 + R-06 + R-11 + 필요한 URF/QoI 적용 확인:** current-attempt evidence, artifact 완료 계약, inlet/설정 적용 확인을 먼저 닫는다. R-02는 아래 V-03의 범위까지 확장해야 한다.
4. **R-03:** 위 완료 계약 위에 retry를 추가한다. 그 뒤 R-05를 적용한다. R-05 개발이 R-03 완료에 논리적으로 의존하는 것은 아니므로 별도 구현은 가능하다.
5. **수정된 R-10 및 R-12:** 실제 mesh/zone 관찰과 tolerance qualification을 거쳐 배선한다. R-09의 extent gate와 같은 readiness 기준으로 묶는다.
6. **workstation 사전 점검 + 작은 end-to-end pilot + interruption/restart test:** V-06의 G/30-cell/URF 점검, 그리고 V-07의 coverage/acceptance를 통과한 실행 문서만 production으로 사용한다.
7. **R-04와 R-13:** 아래 조건에 따라 분리·유예한다. 새로운 meshing이나 repair 경로를 campaign에 포함한다면 R-04는 그 경로 사용 전에 완료한다.

원래 순서의 핵심 의존관계는 동의한다. 다만 “R-03 안의 retry가 반드시 pre-execution skip을 다시 호출한다”는 것은 아직 구현되지 않은 설계다. retry 내부 skip을 한 번만 평가해도 이 문제는 피할 수 있다. 그와 별개로 **driver 재시작**에서 stale finals를 skip하는 결함은 반드시 먼저 고쳐야 하므로 R-02 → R-03 순서는 유지한다.

R-07에서 새 mesh parameters로 `mesh_id`를 생성하는 것과 이미 검증한 31 mesh를 선택하는 것은 다르다. selected `(family, geo_id, mesh_id, mesh_sha256)` 목록을 저장하고 generator가 그 목록에 9 operating points를 붙이도록 한다. `D0817_a60`의 `m_max=0.060` 예외와 exploratory lists는 유지한다. expected `31/279`만 맞고 엉뚱한 mesh leaf가 선택되는 경우를 금지한다.

### V-03 — R-02/R-03/R-05의 완료·재시도 계약은 더 강해야 한다

**가장 큰 계획상 구멍은 정상 stop reason + 이전 artifact 쌍이다.** source `solver_code_260616.py:3733-3749`는 manifest를 finalize한 **뒤에** final files를 쓴다. 새 solve가 `residual_converged`로 finalize하고 write 중 죽었는데 이전 nonempty files가 남으면, 계획의 R-02 조건은 matching mesh hash와 허용 stop reason을 보고 다시 skip한다. stop-reason exception만 고쳐서는 해결되지 않는다. Astra C-02와 Cursor T4-02에 이미 기록된 경로다.

R-02 acceptance criteria를 다음처럼 바꾼다.

- solve 전에 attempt ID와 resolved input fingerprint를 남기고 transcript/report paths도 attempt에 귀속시킨다. 다른 attempt의 marker/G/residual을 탐색 fallback으로 사용하지 않는다.
- 정상 output은 attempt-specific 임시 이름/디렉터리에 저장하고 case/data 쌍을 검증한 다음, 그 exact 쌍을 지목하는 completion record를 **마지막에** atomic publish한다. 파일 두 개의 rename만으로 pair 전체가 atomic이라고 보지 않는다.
- skip은 completion record, exact case/data identity, input fingerprint, 필요한 manifest 상태가 함께 맞을 때만 한다. fingerprint에는 mesh hash뿐 아니라 resolved solver/UDF/template/config identity가 포함된다. hash는 mesh마다 검증 후 같은 immutable batch에서 재사용할 수 있다.
- final write 실패, manifest/completion write 실패, 한쪽 파일만 교체된 상태, 이전 attempt의 terminal manifest는 모두 미완료다. `stop_reason`과 `execution_status`를 구분한다.
- stop reason을 결정하지 못해도 살아 있는 session에서 **복구용 case/data 저장을 시도할 수 있어야 한다.** canonical successful final로 publish하지 않고 failed attempt에 격리한다. R-02의 “어떤 `write_case_data`도 호출하지 않음”은 안전성보다 불필요한 재solve 비용을 만든다. 저장 시도 실패가 원래 exception을 가리면 안 된다.
- failed-state 기록에 정상 finalize의 G/physics 전제조건을 강제하지 않는다. G를 얻기 전에 실패했는데 FAILED 기록을 위해 가짜 G를 넣는 수정은 금지한다. 새 stop enum을 쓰면 허용 집합/parser/소비자를 함께 갱신한다. failure sidecar를 쓰는 것도 가능하다.

R-03은 최대 3 attempt, 새 subprocess/session을 유지한다. **현재 attempt의 root cause**로 분류한다. wrapper가 `LoadBearingReportComputeError`라도 원인이 socket reset이면 transient일 수 있고, 옛 log의 socket 문자열이 현재 UDF/identity failure를 retryable로 바꾸면 안 된다. 같은 semantic failure는 자동 완화하지 않는다. shared workstation에서는 자신이 시작한 session/PID만 정리한다. cleanup 확인 불가를 “프로세스 없음”으로 취급하지 않는다.

R-05의 mtime 비교는 보조 신호다. 나중에 복사한 old CSV는 mtime이 새롭고, `copy2`/백업 복원은 old mtime을 보존할 수 있다. extract completion record에 input case/data fingerprint, layout/window, extractor/config/UDM identity, output 파일 목록을 묶는다. 모든 required CSV/JSON/quality metadata를 쓴 뒤 성공 marker를 publish하고, gate 실패 뒤 남은 CSV는 skip 대상에서 제외한다. `convergence_quality` write 실패는 이 completion을 막아야 한다. 단순히 exit만 nonzero로 바꾸고 valid-looking CSV를 남기면 다음 실행이 또 skip할 수 있다.

추가 fault-injection tests: **old finals + 새 terminal reason + write failure**, one-file write, completion write failure, old transcript에만 G marker 존재, extract가 CSV를 쓴 뒤 validation/quality write 실패, source unchanged이지만 extractor 설정 변경. 성공 fixture 하나와 mismatch fixture 하나만으로는 이 전이들을 검증하지 못한다.

### V-04 — before sweep와 can wait 재배치

| 항목 | 판정 / 최소 범위 |
|---|---|
| finite/NaN 및 layout integer coercion (deferred Sol T3-01/18/19/23/24) | **before sweep로 승격.** 모든 helper의 대규모 정리가 아니라 resolved campaign inputs, load-bearing scalar outputs, gate operands/tolerances의 finite/type 검사만 우선한다. `int(7.9)` truncation, `nan` comparison을 성공으로 처리하지 않는다. |
| current-attempt transcript/complete residual evidence (deferred Sol T2) | **실제 stop/acceptance에 쓰이는 경로는 before sweep.** 잘못된 evidence를 강화된 skip으로 영구 인증하면 R-02가 오히려 오류를 굳힌다. diagnostic-only residual report 전체 개선은 유예 가능하다. |
| `convergence_quality` manifest write 실패 | **before sweep로 승격하여 R-05에 포함.** current quality를 durable하게 기록하지 못하면 extract 완료로 publish하지 않는다. 기존 quality를 현 attempt에 재사용하지 않는다. |
| requested pressure/relaxation 적용 및 QoI monitor 구성 | **before sweep.** V-06 기준으로 actual required settings/monitor set를 확인한다. 요청된 QoI condition 일부가 없는데 `qoi_converged`를 같은 계약으로 인정하지 않는다. |
| actual material/UDF/post constants와 run provenance (Astra A-02/A-06) | **before sweep의 작은 compatibility 확인이 필요.** 사용한 UDF/build 및 template identity, assigned material의 rho/mu/D, post constants와 window를 고정·기록한다. 현재 code constants끼리의 일치만으로 loaded case state가 같다고 인증하지 않는다. 실제 값은 **UNKNOWN**. 전체 설정체계 refactor는 유예 가능하다. |
| `fallback_z_m=0.0` | **무조건 값이 틀렸다는 판정은 하지 않는다.** centered-z 31 meshes라는 계약을 실제 artifact에서 확인하고 같은 mesh hash에 귀속시킨 fallback만 허용하면 유예 가능하다. 현재 그런 확인이 없다면 reduction failure를 live scalar 경로에서 fail-closed로 바꾸는 작은 수정은 before sweep다. |
| R-04 전체 remeshing/repair 도구 | **조건부 유예 가능.** 31 selected mesh가 이미 valid/hash/extent/quality preflight를 통과하고 campaign이 meshing을 호출하지 않으면 `--from-log` 구현이 solver launch의 필수조건은 아니다. repair가 필요한 leaf는 그때 도구를 먼저 만든다. invalid skip을 자동 remesh로 연결하지 말고 `repair_needed`로 멈춘다. |
| R-10의 전역 legacy-path hard gate | **그대로 배선하지 않는다.** 선택된 production IDs/root/coverage 검사로 대체한다(V-05). archive 공존 여부만으로 유효 campaign을 거절할 이유는 없다. |
| R-11 `u_mean_ms*G` identity | 정의 검사는 저렴하므로 함께 해도 좋지만 **metadata-only라 단독으로 launch를 막을 최우선 물리 gate는 아니다.** 실제 inlet flux와 비교하는 검증을 대체하지 않는다. blanket ML/contact/curvature metadata identities도 미사용임이 고정돼 있으면 유예 가능하다. |
| R-12 CSV flux identity의 두 번째 구현 | existing exact-key parser + serialization mapping regression test가 있으면 **중복 live gate는 유예 가능**하다. 서로 다른 tolerance의 복제 구현은 피한다. |
| CP mass-fraction round-trip, 동일 산식 재계산 전부 | **유예 가능.** 같은 rho/MW나 같은 잘못된 cell set로 양쪽을 계산하면 identity는 항상 맞는다. finite/domain/expected-window 검사가 더 중요하다. |
| R-13, figure bounds/partial intersection, z-plane/helper 중복 정리, `_tmp_*` | scalar-only campaign이면 유예 가능. figure를 진단·채택에 쓸 때는 그 경로 검증을 먼저 한다. 중복 제거 자체를 correctness fix와 묶어 범위를 넓히지 않는다. |
| `batch_solver_rerun` skip/promotion/URF | 해당 도구를 campaign recovery에 쓰지 않는 동안만 유예 가능. 쓰기로 하면 R-02/R-05와 같은 artifact/evidence 계약이 **첫 사용 전** 필요하다. |
| `compute_cp_spread=True` 관련 silent zero/bracket/guard | False를 명시해 동결하는 동안만 deferred mechanical로 남긴다. True로 켜는 시점에는 Astra A-01/A-08과 Sol T2의 검증이 선행돼야 한다. False가 scalar-k 근사의 scientific certification을 뜻하지는 않는다. |

CI 자동화 완성은 동일 commit에 대한 full-suite 실행 기록과 workstation smoke를 수동 release gate로 강제할 수 있으면 짧게 유예할 수 있다. full-suite 검증 자체는 유예하지 않는다. postprocessing one-shot `set_state`는 사전 timing pilot에서 비교할 가치가 높지만 correctness 필수조건은 아니다. case-file definition caching은 invalidation 계약이 복잡하므로 이번 필수 수정에 추가하지 않는다.

### V-05 — gate 도입 방식과 “31개에서 0 reject” 기준

**blast radius 보고는 필요하지만 충분하지 않다.** `31 pass`가 “검사하려던 값을 31개 모두 실제로 관찰했다”는 뜻이어야 한다. missing/skipped/not-applicable을 PASS에 합치지 않는다. 진짜 잘못된 leaf 하나를 gate가 거절한 것은 배선을 취소할 이유가 아니라 그 leaf의 provenance/metadata를 수리할 이유다. **false rejection과 genuine mismatch를 분류한 뒤** rollout한다.

#### Extent: R-08/R-09는 migration 방향을 유지하되 provenance를 추가

- general reader는 old manifests를 계속 읽고, campaign preflight만 extent를 요구하는 순서가 맞다. backfill은 **측정 log**에서만 하며 config/registry의 expected length를 measured field에 채우지 않는다.
- 각 값에 source log/path, units, 선택한 measurement block, 대응 mesh hash를 기록한다. 같은 `mesh_id`의 옛 log를 현재 bytes와 임의로 결합하면 equality가 맞아도 provenance는 틀리다.
- `rel_tol=1e-5`는 코드의 기존 값이라는 이유만으로 검증된 허용차가 되지 않는다. 31개에서 absolute/relative residual, log 인쇄 정밀도와 known-past-bug 크기를 보고 정상 반올림은 통과·한 cell/pitch 오류는 reject하는지 확인한다. 지금 적합성은 **UNKNOWN — 실제 logs 필요**.
- `validate_layout_against_x_extent(layout,0,extent)`는 **길이** 검증에는 올바르다. 이것으로 origin=0을 측정 검증했다고 쓰면 안 된다. 서로 다른 `n_active_cells`와 `cell_length_x_m`가 곱을 보존하거나 buffer가 보상하면 length는 맞아도 window가 틀린다. 따라서 31 selected layouts의 개별 count/pitch/buffer/window 계약도 함께 확인한다.
- 31개 backfill 완료는 selected cohort의 readiness만 증명한다. data root의 다른 grid-study/legacy mesh까지 migrated됐다는 뜻이 아니므로 global required-field 승격은 계속 유예한다.
- log가 없거나 현재 mesh와 연결할 수 없어도 바로 remesh하지 않는다. **기존 `.msh.h5`를 Fluent로 읽어 mesh check/extent를 새로 기록**하는 복구를 먼저 쓴다. 실행 가능 여부는 workstation에서 확인한다. 이것은 geometry 재생성이 아니다.
- dry-run 전체 diff/backup/부분 완료 재실행을 지원하고, validation mismatch를 expected value로 자동 교정하지 않는다.

#### 세 dead gate

| gate | 배선 판정 | 필요한 보호 |
|---|---|---|
| `validate_spacer_wall_zones` | **조건부 배선 찬성.** mesh를 load하고 필요한 name refresh 뒤, physics setup/iterate 전에 호출한다. | manifest↔registry만 대조하면 둘 다 같은 registry에서 왔다는 사실만 확인한다. 31 selected meshes를 실제로 load해 성공적으로 얻은 wall-zone inventory를 hash별로 기록한다. split suffix는 실제 naming contract를 확인한 명시적 mapping만 허용하고, branch/hole 누락을 숨기는 무차별 prefix collapse는 금지한다. `REF_empty`는 empty declared list + discovery 실패의 `[]`가 `:298-310`에서 그대로 통과할 수 있으므로 **discovery 성공/일반 boundary inventory nonempty**를 caller가 먼저 확인한다. 하나의 zone 제거, extra spacer zone, hole 유무 오류의 negative tests가 필요하다. |
| `assert_replace_log_matches_mesh_manifest` | **현 함수 그대로 hard gate 배선 반대.** 수정된 보조 provenance check만 허용한다. | `domain_layout.py:432-445`는 sorted logs의 **첫 matching log**를 사용하고 unreadable을 skip한다. `mesh_id` parent만 비교하므로 다른 geo의 동일 `mesh_id`도 통과할 수 있다. 새 solve의 preflight는 아직 생성되지 않은 replacement log를 증명할 수 없다. current-attempt의 explicit log에서 full resolved mesh identity를 읽고 **replacement 이후** 확인한다. extract는 accepted attempt의 기록만 사용한다. log 부재는 NOT_CHECKED이며 다른 신뢰 가능한 artifact provenance가 있을 때만 허용한다. log만으로 byte identity를 인증하지 않는다. |
| `assert_no_legacy_ml_geo_paths` | **전역 root hard gate로는 배선하지 않는다.** 별도 migration audit로 유지 가능하다. | `campaign_geo_ids.py:86-92`는 nonexistent root/tree를 정상 return하므로 env 변수가 있다는 것만으로 검사가 수행되지 않는다. 반대로 무관한 archive `M_r*`가 공존하면 정상 selected campaign도 거절한다. production selection의 canonical IDs, resolved paths, expected 31 mesh set와 root 읽기 성공을 gate한다. 실제 selected path가 legacy이면 reject하되 unrelated archive를 삭제/rename할 필요는 없다. |

31개의 Fluent load는 solver 279회가 아니라 setup-only qualification이다. 이를 생략하고 각 geometry의 첫 production solve에서 exact-set gate를 처음 시험하면 “중간에 멈추지 않기”라는 목적을 달성하지 못한다. G/URF/material checks를 같은 setup session에 묶어 비용을 줄인다.

#### R-11/R-12 identities가 증명하는 범위를 좁혀라

- `u_mean_ms*G==u_target_ms`는 legacy coefficient bookkeeping 검사다. 둘을 같은 G로 만들면 성립하므로 실제 applied inlet을 증명하지 않는다. 기존 per-mesh G 합의와 서로 다른 meshes 간 campaign 비교를 구별한다. 두 측정 G의 차이 약 `3.2e-6`를 이미 존중하면서 **cross-mesh**에 `1e-6` tolerance를 무차별 적용하면 정상 값도 거절한다.
- `area_mem≈pp_udm_area_sum`은 측정 합의 `1e-15`에 근거해 `rel_tol=1e-9`를 **초기 qualification 후보**로 둘 수 있다. 두 값의 finite/positive, 동일 membrane scope, area accumulator 초기화·갱신 시점을 확인하고 representative families에서 residual을 기록한 뒤 production gate로 고정한다. 전체 면적 합의는 per-face CP interpolation의 동등성을 증명하지 않는다. 실제 family별 오차는 **UNKNOWN**이다.
- `pressure_drop_spacer≈sum(active-cell dP)`는 **active span**으로 하는 것이 맞다. 그러나 내부 pressure terms가 telescoping으로 소거되므로 잘못된 내부 plane 위치를 일반적으로 검출하지 못한다. 양쪽 endpoints까지 같은 wrong layout에서 만들면 전부 PASS한다. 이 gate는 R-09나 exact evaluation-window 검사의 대안이 아니다. 반환 dP가 큰 absolute pressure의 차라면 tolerance는 pressure precision/반올림과 별도 report 계산 오차를 고려한 absolute+relative 기준으로 사전 qualification한다. 임의 tolerance로 279회 중 처음 trip할 때 해석하지 않는다.
- flux identity는 exact requested key와 동일 sibling payload라는 parser 계약을 유지한다. inlet/outlet을 함께 바꿔도 각 decomposition 합은 맞을 수 있으므로 requested report→CSV mapping fixture가 필요하다. 실제 zero-source component를 invalid로 취급하지 않는다.
- `cp_canon_window_avg == (window_cm-window_cp)/(window_cb-window_cp)`를 새 hard gate로 만들지 않는다. 평균의 비와 비의 평균은 일반적으로 같지 않다. Cursor T3-16은 계속 NEEDS-ASTRA다.
- 모든 새 gate는 **어느 값이 독립 관측이고 어떤 잘못을 잡는지** 명시한다. missing/non-finite/읽기 실패/정상 경계값/실제 과거 bug를 포함한 tests를 쓰고, production을 통과시키기 위해 threshold를 넓히지 않는다.

### V-06 — 요청한 세 open question의 결정

| 질문 | 279-run 시작을 막는가 | 가장 싼 해결 |
|---|---|---|
| `INLET_G_MAX=1.02`가 모든 mesh를 덮는가 | **band 변경은 필요하다고 입증되지 않았다. 유지한다.** 두 측정 `1.00360939613`, `1.003612623`은 강한 근거이나 `D0817_a60`을 포함한 전31개 증명은 아니다. fail-closed이므로 곧바로 silent corruption은 아니지만 unattended 279의 준비 완료에는 미확인 항목이다. | 먼저 `D0817_a60`의 실제 mesh/UDF로 initialization 및 profile hook이 평가될 최소 단계까지만 실행해 **current-attempt** G success marker, inlet area, physical boundary flux를 기록한다. 31 setup-only qualification에 같은 검사를 묶는다. 전체 solve/새 mesh는 필요하지 않다. out-of-band이면 marker/area/hook/좌표·units를 조사하며 band부터 넓히지 않는다. 실제 G는 **UNKNOWN — live Fluent 필요**. |
| 30-cell Fluent report/surface 한도인가 | **전체 unattended sweep 전 운영 검증을 막는다.** 한 Scheme fault로 hard limit라고 단정할 수도, unrelated transient라고 치부할 수도 없다. | `D0817_a30`에서 실제 production flags로 create→compute→segmented CP→CSV까지 한 번 완주시키고, fresh session으로 최소 3회 반복하여 중간 크기/10-cell control과 phase·peak memory·resident objects·cleanup·retry 기록을 비교한다. 기존 solved pair가 없다면 작은 pilot으로 유효한 field data를 먼저 만들며 이 실행을 scientific validation으로 세지 않는다. 반복 성공은 hard cap 가설에 반증을 주지만 낮은 crash probability까지 증명하지는 않는다. 실패가 크기/특정 phase와 재현되면 settings calls/동시 object 수를 줄인 경로를 검증한 뒤 시작한다. 실제 원인은 **UNKNOWN — live Fluent 필요**. |
| pressure-relaxation 적용 실패 시 abort | **명시적으로 requested한 active setting의 확인 실패는 before sweep에 abort하도록 정한다.** 모든 non-`APPLIED_CONFIRMED`를 일괄 abort하는 Claude 처방은 과도하다. | 현재 campaign의 exact solver mode/profile로 setup까지만 실행해 requested/before/after/status를 durable하게 저장한다. `conservative`/`strong`의 `explicit_pressure_under_relaxation`, momentum, 적용 대상 species setting은 expected exact set를 구성하고 confirmed readback을 require한다. `baseline`, 명시적 `preserve`는 적용 실패가 아니며 actual effective state를 기록한다. verbosity는 diagnostic이므로 failure가 solver를 막을 이유가 없다. setting이 그 mode에 실제로 미적용 대상이면 사전에 명시적으로 NOT_APPLICABLE로 승인·기록하고, discovery 실패를 NOT_APPLICABLE로 바꾸지 않는다. 필요한 API/mode 지원 여부는 **UNKNOWN — live Fluent readback 필요**. |

pressure-relaxation의 이유는 “URF가 다르면 반드시 최종 물리가 달라진다”는 주장이 아니다. 현재 종료 기준과 finite iteration budget 아래서 지정한 protocol을 실제로 실행했는지 알 수 없기 때문이다. 해결을 위해 279개를 비교 solve할 필요는 없다. 기존 `set_and_verify_leaf`의 readback outcome을 **필수 항목 목록과 대조하고 저장**하면 된다. `SKIPPED_SPECIES_UNAVAILABLE`를 species가 필요한 campaign에서 자동 성공으로 인정하지 않는다. 이 판단에 따라 “URF abort는 deferred” 문구를 위 범위에 한해 철회한다.

30-cell 수량도 단위를 정정해야 한다. 요청의 같은 phase 기준 `76`(10-cell), `132`(24-cell), boundary당 `4` definitions를 적용하면 30-cell은 **156**의 산술 외삽이다. 계획의 `~215`는 다른 phase/집계 범위와 섞였을 수 있다. segmented objects의 lifetime/peak는 별개이므로 이 숫자를 Fluent hard limit나 전체 비용으로 쓰지 않는다. instrument한 실제 count가 실행계획의 기준이다.

### V-07 — 네 review를 종합해도 남는 공통 사각지대

**새 source bug를 추가로 확정한 것은 없다.** 문서에서 보이는 공통 설계 사각지대는 다음과 같다.

1. **“279 jobs가 성공”과 “279 comparable results가 채택 가능”의 혼동.** R-07은 입력 cardinality, R-02는 solver 완료, R-05는 process exit를 다룬다. 마지막 output의 exact four-ID set/uniqueness와 current quality/metric scope를 묶는 release criterion이 없다. `max_iter_reached`는 재solve 자동 반복을 피하려는 completed-computation skip으로는 허용 가능하지만 scientific acceptance가 아니다. `FAIL/UNKNOWN` 또는 미해결 warning을 `use_for_final_comparison=True`로 만드는 blacklist 방식은 금지한다(Astra A-02 끝 행). LMH/CP/dP의 exact CSV columns, units, window를 정한 explicit acceptance record가 필요하다. `n_lead_excluded=3`은 유지한다.
2. **한 current generation 전체의 provenance.** mesh SHA를 검사해도 그 mesh로부터 만들어진 case/data라는 사실, 사용된 UDF/template/config, CSV가 그 data에서 나왔다는 사실은 자동으로 연결되지 않는다. 이 위험의 부품은 Astra A-05/C-02에 있었지만 R-02/R-05/R-10을 합쳐도 빠진다. V-03의 attempt/input/output completion 계약으로 해결한다. same-name 파일을 hash만 새로 계산해 current라고 승인하지 않는다.
3. **shared workstation에서 동일 leaf에 둘이 쓰는 경우.** notes에는 review 파일 자체의 동시 덮어쓰기 경험도 있지만 production driver의 concurrent run ownership은 검증돼 있지 않다. 두 driver가 같은 four-ID로 실행될 때의 lock/claim 존재는 **UNKNOWN — 이번 범위에서 source를 추가 탐색하지 않았다**. 새 defect로 단정하지 않는다. campaign 운영을 single-writer로 고정하거나 per-leaf claim을 확보하고, stale-lock 복구와 서로의 Fluent process를 종료하지 않는 규칙을 문서화한다.
4. **mechanical green을 scientific go로 승격하는 위험.** CP discriminability, near-wall resolution, local `c_b`/aggregation, empty-channel discrepancy, `u=0.3` 안정성은 Astra B에 이미 있었으므로 새 발견은 아니다. 하지만 이 계획은 이 질문들의 owner/판정 시점 없이 모든 R 완료를 sweep readiness처럼 읽히게 한다. mesh나 solver protocol이 바뀔 수 있는 unresolved scientific question은 **전체279 승인 전에 별도 sign-off 또는 명시적 pilot 한정**이 필요하다. 이 문서 검토만으로 numerical adequacy는 **UNKNOWN**이며 승인하지 않는다. 가장 싼 다음 단계는 이미 있는 grid/velocity 결과를 최종 canonical column/window로 재집계해 변화 가능성이 큰 항목을 좁힌 뒤, 필요한 matched-pair refinement 또는 `u=0.3` pilot만 수행하는 것이다.

### 실행 승인 조건

R-01 green 이후에도 곧바로 production을 시작하지 않는다. **선택된 31 mesh의 measured/provenance qualification, 보강된 R-02/R-05의 failure/restart 검증, requested physics/numerics 적용 확인, 30-cell end-to-end pilot, exact 279 coverage와 acceptance 규칙**이 준비된 뒤 시작한다. 보호 결정은 모두 유지한다. 이 판정으로 추가한 gate의 목적은 기존 유효 데이터를 무차별 거절하는 것이 아니라, 관측하지 않은 항목을 PASS로 기록하지 않도록 하는 것이다.

