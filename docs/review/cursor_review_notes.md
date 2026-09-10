# Cursor repository review — mechanical first pass

Astra `docs/review/astra_review_notes.md`는 읽기 전용. A1 source-of-truth 표는 재작성하지 않음. 아래는 그 외 전수 항목의 확장.

존중하는 결정: `membrane_blocked_area_frac=0.0`, flux `(without-sources)`, `periodic_after_surface_mesh=True`, extract meshing→`switch_to_solver()`, `u_mean_ms=u_target/G`, `n_lead_excluded=3`.

`C:/ro_data` 및 live Fluent는 이 머신에서 접근 불가.

## Progress

- T1 hardcoded layout: **COMPLETE**
- T2 except clauses: **COMPLETE** (live `scripts/` workers + 전부 `src/ro/`. `diagnose_*`/`probe_*`/`_tmp_*`/`analysis/`는 campaign gate 아님 — 한 줄로 묶음)
- T3 arithmetic identities: **COMPLETE**
- T4 retry/skip/recovery: **COMPLETE**
- T5 hygiene: **COMPLETE**
- T6 test suite: **COMPLETE** — 1056 passed, 3 failed, 1 skipped

## Findings

| id | task | file:line | what | what breaks | triage |
|---|---|---|---|---|---|
| T1-01 | T1 | `src/ro/convergence_quality.py:33`, `:88-92` | `PRESSURE_DROP_CELLS=(4,5,6,7)` 및 `pressure_drop_rel_spread_cells_4_7`. window spread는 layout에서 유도됨. 이 열은 고정 global 4–7. | 1+7+2에서 excluded cell 4를 넣고 window cell 8을 뺌 (이미 알려진 교정 사례). 1+27+2(30-cell)에서는 4–7이 존재하므로 crash 없음. 숫자는 window의 극히 일부만 보고. | CLEAR — continuity 열을 유지하려면 이름을 고정 범위로 명시하고, scoring은 이미 있는 `pp_pressure_drop_rel_spread_window`만 쓸 것 |
| T1-02 | T1 | `src/ro/fluent_report_helpers.py:2568-2574` (legacy twin `:2531-2536`) | `pp_pressure_drop_cell2_over_cell3`가 global cell 2, 3에 고정. 주석: "Intentionally fixed to cells 2 and 3". | `n_buffer_in=1`이면 첫 두 active cell이라 Diamond 5–27에서도 crash 없음. `n_buffer_in≠1`이면 잘못된 cell. 279-run은 buffer_in=1이라 틀린 숫자만 가능(entrance diagnostic). | CLEAR — `layout.active_cell_numbers()[:2]`에서 유도 |
| T1-03 | T1 | `src/ro/fluent_report_helpers.py:2527` | legacy `derive_periodic_spacer_pressure_metrics`가 `cell_length_m=domain_length_m/n_unit_cells`. asymmetric buffer에서는 pitch가 아님. | live extract는 `_for_layout` (`:2564`)를 씀. 이 함수를 직접 호출하면 periodic_per_m이 틀림. crash 아님. | CLEAR — live path는 이미 layout pitch. legacy 함수를 dead로 표시하거나 삭제 후보 (T5) |
| T1-04 | T1 | `src/ro/fluent_report_helpers.py:790-807` vs `:874-907` | `LOAD_BEARING_REPORT_NAMES`에 per-cell `pp_p_unit_cell_boundary_*` / mixing-cup가 없음. `expected_cell_7_report_names(n)`은 `n_unit_cells`로 경계를 만듦. | 30-cell에서 경계 report가 늘어도 load-bearing gate는 전역 12개만 봄. Cell 8 except가 그 이름을 `None`으로 넣고 0 종료 가능 (T2). crash보다 잘못된/빈 cell dP. | CLEAR — evaluation-window 경계 report를 load-bearing에 넣거나, `None` cell dP를 extract nonzero로 승격 |
| T1-05 | T1 | `src/ro/domain_layout.py:53-55,298-305` | `CELL_LENGTH_X_M=0.003465`, `CURRENT_LAYOUT` n_active=7. live `layout_from_mesh_manifest`는 payload에서 읽음. | production extract/solver는 manifest 경로. `CURRENT_LAYOUT`를 쓰는 것은 tests와 `test_solver_qoi_reports.py` fixture. 30-cell live crash 아님. | CLEAR — live fallback이 아님. 상수 이름을 `D2450_A45_LAYOUT`처럼 고정 geometry로 명시하면 오용 방지 |
| T1-06 | T1 | `src/ro/campaign_geometry.py:67-77,277-289` | Diamond `n_active_cells`는 `_DIAMOND_LAYOUTS`에서 옴 (5–27). ML/Pillar/Sin/REF는 상수 7. | registry 자체는 Diamond에 맞음. 문제는 A-02: mesh payload의 config 값이 registry를 덮음. `_COMMON_MESH`의 7이 Diamond에 붙으면 registry 21/27이 패배. | CLEAR — Diamond meshing case는 `_DIAMOND_LAYOUTS`의 n_active/pitch를 반드시 config에 넣고 `_COMMON_MESH` 기본값을 상속하지 말 것 |
| T1-07 | T1 | `configs/batch_config.py:132-155,147-148,488-498` | `_COMMON_MESH`가 `n_active_cells=7`, `cell_length_x_m=0.003465`. `_DIAMOND_MESH_LAYOUTS`는 주석 "not used by this config"이며 `mesh_batch_cases`에 append되지 않음. | Diamond를 `_COMMON_MESH` copy로 넣으면 T1-06과 동일. 현재 22-case mesh list는 ML/Pillar/Sin/empty만 7-cell이라 그 경로에선 맞음. 279-run generator가 이 dict를 물면 D0817_a30이 7-cell로 mesh. crash 아님, 잘못된 domain. | CLEAR — Diamond case dict에 `_DIAMOND_MESH_LAYOUTS`의 n_active/pitch/`m_max`를 넣고 `mesh_batch_cases`에 연결. D-02와 동일 뿌리 |
| T1-08 | T1 | `scripts/pyfluent_report_extract.py:948,1129,1519,1617` | Cell 7/8 경계 루프는 `layout.n_total` / `unit_cell_boundary_x_m`. 고정 10 없음. | 30-cell에서 경계 31개 × report 4개. 10-cell만 실행됨. 이름/루프는 맞음. Fluent named-object 한도·시간은 미검증. | UNKNOWN — live Fluent에서 30-cell extract가 report 생성/compute를 끝까지 도는지 |
| T1-09 | T1 | `scripts/verify_load_bearing_report_names.py:22-25` | CLI `--n-unit-cells` default 10. | 캠페인 live path 아님. default로 돌리면 30-cell 전용 이름은 검사 대상이 아님 (`LOAD_BEARING`에 per-cell 이름 없음). | CLEAR — 기본값을 `layout.n_total` 인자 필수로 바꾸거나 Diamond max로. 유예 가능 |
| T1-10 | T1 | `scripts/solver_code_260616.py:3424-3428,1657-1668` | QoI spacer 평면은 `layout_from_mesh_manifest` → `active_span`. 고정 7/10 없음. | 30-cell crash 없음 (전역 in/out 두 평면). | CLEAR — 조치 없음 |
| T1-11 | T1 | `src/ro/fluent_report_helpers.py:790-807` + extract `:2434` | T1-04 정정: window cell dP가 `None`이면 `pp_pressure_drop_periodic_per_m`가 `None` → `require_load_bearing_summary_columns`가 artifact 기록 후 raise. | Cell 8이 window 경계 report를 `None`으로 넣어도 extract는 최종에 nonzero. 남는 hole은 excluded cell 2–4 및 continuity 4–7. | CLEAR — T1-04의 "silent 0 종료"는 window 점수에 대해선 현재 코드에서 막힘. excluded-cell 공백은 유예 |
| T1-12 | T1 | `udfs/260822_RO_UDF.c`, `scripts/make_summary_figures.py`, `scripts/pyensight_contour_export.py`, `scripts/meshing_code_260616.py` | streamwise cell count 하드코드 없음. UDF의 7/10은 UDM index. figures의 `n_total`은 CSV row count. | Diamond 30-cell에서 이 파일들로 인한 잘못된 cell index 없음. | CLEAR — 조치 없음 |

## T2 — except (Astra A-07/A-08/A-09 확장)

분류: FATAL-CORRECT / SILENT-BLANK / SILENT-STALE / FALLBACK-OK.

| id | task | file:line | what | what reaches output | class | triage |
|---|---|---|---|---|---|---|
| T2-01 | T2 | `scripts/pyfluent_report_extract.py:1300-1310` | Cell 8 compute except. `LOAD_BEARING_REPORT_NAMES` 또는 flux massflow면 `LoadBearingReportComputeError` re-raise. 그 외 이름은 `computed_values[name]=None`, raw `error`. | A-07 교정 사례(mass-closure `None` + exit 0)는 **현재 코드에서 막힘** (`pp_m_in`/`pp_m_out` load-bearing). 비 load-bearing (per-cell p, mixing-cup, `pp_jw_max` 등)은 `None`. mixing-cup `None`은 이후 mid-plane assert가 KeyError (FATAL). window dP `None`은 T1-11. | 비 LB: SILENT-BLANK; LB: FATAL-CORRECT | CLEAR — Cell 8 mass-gate는 수정됨. 남은 SILENT-BLANK는 diagnostic/min/max. 캠페인 점수 열은 최종 gate |
| T2-02 | T2 | `scripts/pyfluent_report_extract.py:1226-1228,1250-1252` | membrane/volume report **생성** 실패를 Warning + `failed_report_specs`. 이후 `require_load_bearing_report_definitions`. | LB 생성 실패 → raise. `pp_jw_max` 등 비 LB는 이름 없이 진행, Cell 8에서 그 키 없음 → `None`. | LB: FATAL-CORRECT; 비 LB: SILENT-BLANK | CLEAR — min/max diagnostic만. 유예 |
| T2-03 | T2 | `scripts/pyfluent_report_extract.py:1360-1368` | salt mass-fraction reduction except. diagnostics dict가 이미 `None`으로 채워짐. error string만 기록. | `pp_salt_mass_fraction_*` 전부 `None`. extract 계속, 최종 LB summary에 이 열 없음. | FALLBACK-OK | CLEAR — saturation diagnostic. 유예 |
| T2-04 | T2 | `scripts/pyfluent_report_extract.py:1417-1422,1496-1507,1659-1672` | mid-plane 생성/window c_b/segmented CP 실패는 RuntimeError re-raise. | 성공 없음. | FATAL-CORRECT | CLEAR — 조치 없음 |
| T2-05 | T2 | `scripts/pyfluent_report_extract.py:1593-1600` | legacy whole-domain center-plane. except → diag string, `c_bulk_center_area_avg`는 `None`. | inventory 전용. canonical CP와 분리됨. | FALLBACK-OK | CLEAR — 유예 |
| T2-06 | T2 | `scripts/pyfluent_report_extract.py:433-453` | `list_named_object_names` 3단 fallback 후 `[]`. A-09 확장. | 빈 목록. membrane/inlet 빈 목록은 `:867-876`에서 raise. `wall` 타입 getattr 실패 시 spacer_wall_zones=`[]`이고 **raise 없음**. | membrane 경로: FATAL-CORRECT; spacer 목록: SILENT-BLANK | CLEAR — spacer 비면 Diamond/ML에서 spacer-named reports만 빠짐. extract가 spacer walls를 require하지 않음. 현재 load-bearing은 spacer walls 불필요 |
| T2-07 | T2 | `scripts/pyfluent_report_extract.py:478,500` | BC/cell-zone 타입 getattr 실패 → `continue` (그 타입 생략). | 해당 타입 키 없음. wall 생략이면 membrane find 실패 → raise. fluid 생략 → raise. | 필수 존: FATAL-CORRECT | CLEAR — A-09와 같음. empty vs error는 로그로만 구분 |
| T2-08 | T2 | `scripts/pyfluent_report_extract.py:556-558` | report delete 실패. `False` 반환 후 create/update 시도. | 기존 정의가 남을 수 있음. 이후 update path. | FALLBACK-OK | NEEDS-ASTRA — stale report definition이 update로 덮이는지 Fluent 세션에서 확인 |
| T2-09 | T2 | `scripts/pyfluent_report_extract.py:2456-2460` | `update_run_manifest_fields` 품질 기록 실패. WARNING. CSV는 이미 씀. extract 0 가능. | manifest의 `convergence_quality`가 이전 값 또는 없음. CSV는 현재. inventory가 manifest를 보면 STALE. | SILENT-STALE | CLEAR — 실패 시 nonzero. 279 전에 inventory가 어느 쪽을 보는지 확인 (T4) |
| T2-10 | T2 | `scripts/pyfluent_report_extract.py:22,303-316,335,717-734,2497-2504,2509-2537` | Jupyter display fallback; layout unset → ValueError; JSON DecodeError raise; TUI iso 실패 raise; 본문 except re-raise; timing/exit cleanup warning. | display: print. layout/JSON/iso/본문: 실패. cleanup: 무시. | FATAL-CORRECT 또는 FALLBACK-OK | CLEAR — 조치 없음 |
| T2-11 | T2 | `src/ro/fluent_report_helpers.py:35-55` | extract와 동일 `list_named_object_names` → `[]`. | 호출자가 빈 목록을 실제 empty로 취급. | SILENT-BLANK | CLEAR — A-09. 필수 discovery는 이미 일부 fail-closed |
| T2-12 | T2 | `src/ro/fluent_report_helpers.py:71,120,1434-1438,1633-1640,1800-1807` | iso/report delete 실패 `pass`. | 세션에 stale surface. 다음 create가 덮거나 TUI fallback. | FALLBACK-OK | NEEDS-ASTRA — T2-08과 동일, Fluent overwrite |
| T2-13 | T2 | `src/ro/fluent_report_helpers.py:99,148,689,706,1044` | plane TUI 실패 / zone resolve 실패는 raise. | 성공 없음. | FATAL-CORRECT | CLEAR |
| T2-14 | T2 | `src/ro/fluent_report_helpers.py:242-247` | z-bound reduction 실패 → `fallback_z_m=0.0`. | 캠페인 origin이 0이면 맞음. translated mesh면 벽 평면. | SILENT-BLANK (잘못된 z) | CLEAR — measure 실패는 raise. campaign origin0이라 현재 CAD는 OK |
| T2-15 | T2 | `src/ro/fluent_report_helpers.py:1624-1626` | CP spread iso-clip area compute 실패 → `sub_area=0.0`. | quantile이 0면적으로 진행. `compute_cp_spread=False`가 기본이라 live 기본 경로 비활성. | SILENT-BLANK | CLEAR — flag를 True로 켜기 전에 0.0을 실패로 승격 |
| T2-16 | T2 | `src/ro/manifest.py:499,523,534,582` | path/JSON/write 실패 → `ManifestError`. | 성공 없음. | FATAL-CORRECT | CLEAR |
| T2-17 | T2 | `src/ro/manifest.py:824-825` | `_iter_child_dirs` OSError → `[]`. A-09. | 그 하위 트리 전체가 없는 것처럼 스캔. | SILENT-BLANK | CLEAR — 필수 inventory는 접근 실패를 raise해야 함. 279 전 (누락 run) |
| T2-18 | T2 | `src/ro/manifest.py:874-884` | `skip_invalid=True`면 불량 manifest continue. | 그 run 생략. caller 기본값 확인 필요. | skip 옵션: FALLBACK-OK; 기본 raise: FATAL-CORRECT | CLEAR — live inventory 기본 `skip_invalid`를 T4에서 확인 |
| T2-19 | T2 | `scripts/solver_code_260616.py:3664-3667,3753-3759` | `calculate`와 outer except는 re-raise. | 성공 없음. | FATAL-CORRECT | CLEAR — A-07 대조. 이 둘은 silent 아님 |
| T2-20 | T2 | `scripts/solver_code_260616.py:3686-3690,3727-3731,3733-3749` | A-07 여전히 존재. stop reason except → WARNING, `solver_stop_reason` None이면 finalize skip, 그래도 `write_case_data` 진행. artifact 있으면 exit 0. | RUNNING/미완성 manifest + final files. | SILENT-STALE | CLEAR — 279 전. unknown/failed로 finalize하고 nonzero |
| T2-21 | T2 | `scripts/solver_code_260616.py:1204-1214` | inlet TUI fallback except 후 그래도 `Inlet BC set` 출력. | 설정 실패인데 성공 로그. profile UDF 미적용 가능. | SILENT-STALE | CLEAR — except 후 raise. 279 전 (parabolic inlet) |
| T2-22 | T2 | `scripts/solver_code_260616.py:528-547` | `(update-solver-thread-names)` 3단 실패 후 Warning만. | THREAD_NAME 미설정. UDF가 이름 조회에 의존하면 membrane hook 실패. | SILENT-BLANK | NEEDS-ASTRA — 현재 UDF가 THREAD_NAME(t)를 쓰는지. 쓰면 279 전 |
| T2-23 | T2 | `scripts/solver_code_260616.py:1717-1728` | QoI spacer pressure 생성 실패 → WARNING, 솔버 계속. | pressure QoI 모니터 없음. residual/max_iter로만 정지 가능. | FALLBACK-OK | CLEAR — 수렴 판정이 QoI에 의존하면 T4에서 재분류 |
| T2-24 | T2 | `scripts/solver_code_260616.py:113-114` | case-local UDF 없으면 repo `udfs/` parse. | `analytic_cwall`이 case-local이 아닌 repo 값. | SILENT-STALE | CLEAR — missing case-local은 finalize에서 이미 다른 경로로 막을 수 있음. 파일 없음 vs 파싱 성공 구분 |
| T2-25 | T2 | `scripts/solver_code_260616.py:617-637,662,688,2185-2330` | zone list `[]`; URF apply WARN 후 이전 값. | zone: A-09. URF: 옛 relaxation으로 계산. | zone: SILENT-BLANK; URF: SILENT-STALE | NEEDS-ASTRA — URF 실패를 hard fail할지 |
| T2-26 | T2 | `scripts/meshing_code_260616.py:244,486,1152-1154` | JSON/remove/본문 except raise. | 성공 없음. | FATAL-CORRECT | CLEAR |
| T2-27 | T2 | `scripts/meshing_code_260616.py:558-575,593-598` | label read 실패 → `[]` 후 pre-check skip. task exists except → False (insert 시도). | 잘못된 label이 Fluent까지 감. task probe 오탐이면 insert/raise. | SILENT-BLANK (label skip) | CLEAR — empty available labels를 skip이 아니라 fail |
| T2-28 | T2 | `scripts/meshing_code_260616.py:145-146,175-208,1162,1170,1201,1209` | watchdog read `""`; teardown warning; transcript/ledger/chdir ignore. | ledger 실패해도 mesh+manifest는 이미 성공일 수 있음. | FALLBACK-OK | CLEAR — ledger는 유예. manifest 성공이 source of truth |
| T2-29 | T2 | `src/ro/cp_metrics.py` | `except` 없음. A-08은 except가 아니라 실패한 bisection이 upper bound를 성공처럼 반환. | 그럴듯한 scalar. `compute_cp_spread=False`면 live 비활성. | (except 아님) | CLEAR — A-08 유지. flag on 전에 미수렴을 fail로 |
| T2-30 | T2 | `src/ro/solver_common.py:212-245` | 비수치 `max_iterations`/`residual_target` → fallback 상수 + WARNING. | inventory 보고 기준이 run과 다를 수 있음 (A-02). | SILENT-STALE | CLEAR — malformed common settings는 raise |
| T2-31 | T2 | `src/ro/residual_transcript.py:76,183,556,705` | OSError/`PARSE_UNREADABLE`/outer except → null row, 배치 계속. | 그 case 잔차 측정 공백. residual_measurement_report는 campaign gate 아님. | FALLBACK-OK | CLEAR — 유예 |
| T2-32 | T2 | `src/ro/domain_layout.py:438-439` | replace-log OSError → `continue`. 다음 로그 또는 None. | A-05: 첫 읽힌 로그의 last msh.h5. 최신 로그가 깨지면 이전 로그. | SILENT-STALE | CLEAR — 읽기 실패를 mismatch로. 유예(로그는 보조) |
| T2-33 | T2 | `src/ro/shear_cff_mu_guard.py:33,82` | parse/guard except → skip. | contour mu 불일치 미검출. 그림 경로. | FALLBACK-OK | CLEAR — 비교 그림 전에 fail (A-02) |
| T2-34 | T2 | `src/ro/mesh_common.py:231,558,780` | 이름/JSON parse 실패 → `None`/`UNAVAILABLE`. | ledger provenance 공백. | FALLBACK-OK | CLEAR — 유예 |
| T2-35 | T2 | `scripts/batch_report_extract.py:33,129,472` | layout resolve 실패 → case FAILED. CSV read 실패 → invalid → re-run. subprocess except → FAILED. | 성공으로 안 기록. | FATAL-CORRECT | CLEAR |
| T2-36 | T2 | `scripts/batch_solver_rerun.py:1240` 및 URF/TUI best-effort 다수 | prior SUCCESS CSV 읽기 실패 → skip-success 비활성(재실행). `set_and_verify_*`는 재읽기 없이 성공 안 함. | skip 실패는 재실행(보수). TUI best-effort는 설정 미적용 가능. | CSV: FALLBACK-OK; TUI: SILENT-STALE | NEEDS-ASTRA — rerun TUI 실패를 hard fail할지 |
| T2-37 | T2 | `diagnose_*`/`probe_*`/`_tmp_*`/`scripts/analysis/` | 다수 except가 print 후 계속. | campaign CSV/manifest에 안 씀. | FALLBACK-OK | CLEAR — 279 경로 아님. 전수 line 목록 생략 |

## T3 — 데이터에 있는 산술 identity, 미검사

이미 있는 것 (누락 목록에서 제외): `n_lead+n_trail < n_active`; layout/config alias conflict raise; mid-plane c_b vs mixing-cup; LMH/mass-balance relative gates in `evaluate_convergence_quality`; blocked **range** `[0,1)`.

| id | task | identity | where it should live | past bug if any | triage |
|---|---|---|---|---|---|
| T3-01 | T3 | `layout.total_length_m == domain_extent_x_max - domain_extent_x_min` (`buffer_in + n_active*pitch + buffer_out`, **not** `cell_length_x_m * n_total`) | `validate_layout_against_x_extent` 존재, **tests만**. mesh write + extract preflight에서 `ok`가 아니면 raise | A-04. pitch/buffer/count mismatch. naive `n_total*pitch`는 buffer_out=2*pitch일 때만 우연히 맞음 | CLEAR — live gate. helper를 raise로 |
| T3-02 | T3 | `membrane_blocked_area_frac == 0.0` (campaign policy) | `manifest_validation.py:562`와 `lmh_metrics.py:28`은 `[0,1)`만 | consumed에 geometric 0.15를 넣으면 LMH 분모가 family마다 달라짐. 현재 registry는 0 | CLEAR — policy `==0.0`. 값 0.0 존중, 검사만 추가 |
| T3-03 | T3 | `u_mean_ms * inlet_profile_G == u_target_ms` (legacy 정의) | `finalize_worker_run_manifest` | 기존 잘못된 `u_mean_ms` 보존 (`solver_code:103-104`) | CLEAR — 정의 바꾸지 말고 곱 검사 |
| T3-04 | T3 | mesh vs run `membrane_blocked_area_frac` 동등 | run write / extract preflight | A-02. extract는 run, solver QoI는 mesh | CLEAR |
| T3-05 | T3 | `pressure_drop_spacer == p_spacer_in - p_spacer_out` (재계산) | extract after `get_value` | Fluent expression이 stale definition을 가리키면 열만 그럴듯 | CLEAR |
| T3-06 | T3 | `pressure_drop_spacer_per_m * spacer_length_m == pressure_drop_spacer` | extract | `safe_divide`와 spacer_length 불일치 | CLEAR |
| T3-07 | T3 | `pp_pressure_drop_periodic_per_m == sum(window dP) / (n_window * cell_length_x_m)` | extract after derive | T1-03 legacy `domain_length/n_total` 버그 재발 검출 | CLEAR |
| T3-08 | T3 | `n_buffer_in + n_active_cells + n_buffer_out == n_total` 저장값 / `n_unit_cells` alias | manifest read | config 7 vs Diamond 27 | CLEAR — T1-06과 연결 |
| T3-09 | T3 | `len(evaluation_cell_numbers) == n_active - n_lead - n_trail` | extract | window 공백 | CLEAR — EvaluationWindow가 이미 보장, persist 값과 재계산 비교 |
| T3-10 | T3 | `run_id` 토큰 vs `u_target_ms`, `p_gauge_pa` (`u0p2_p6M`) | sweep/extract preflight | A-02. batch_report가 u/p override 가능 | CLEAR |
| T3-11 | T3 | `mesh_sha256` vs `.msh.h5` bytes | solver/extract preflight | A-05. 파일 교체 | CLEAR |
| T3-12 | T3 | `domain_extent_x_m == x_max - x_min` (세 필드가 모두 있을 때) | manifest_validation | min 누락 시 `.get(...) or 0.0` (diagnose 스크립트) | CLEAR |
| T3-13 | T3 | transcript `inlet_profile_G` vs mesh 저장 G | finalize | B-09/A-10 범위. 불일치는 잘못된 u_mean | CLEAR — 값 바꾸지 말고 equality |
| T3-14 | T3 | `lmh_mass_balance` Fluent vs python (`m_in`,`m_out`,area,blocked) | extract; `lmh_relative_difference`는 이미 gate | 표현식/필드 drift | CLEAR |
| T3-15 | T3 | `water_sink == total_sink - salt_sink` (UDM1=UDM2-UDM0) | extract | UDM_SM 제거 후 재도입 오류 | CLEAR |
| T3-16 | T3 | `cp_canon_window_avg` vs `(cm-cp)/(cb-cp)` from stored window columns | extract | 면적가중 vs ratio-of-avgs 재발 | NEEDS-ASTRA — CSV에 cm/cp/cb가 같은 aggregation인지 |
| T3-17 | T3 | `family` vs `geo_id` prefix (`D*` diamond, `REF_empty`→empty) | manifest location check는 dir 일치만 | 잘못된 family tree | CLEAR |
| T3-18 | T3 | `porosity_eps` mesh measured vs run (run이 None으로 덮지 않음) | run merge | A-02 porosity | CLEAR |

## T4 — retry / skip / recovery

| id | task | stage | transient retry | permanent | skip | skip = file exists? | dead end | triage |
|---|---|---|---|---|---|---|---|---|
| T4-01 | T4 | `batch_meshing.py:119-126,146-157,332-378,584-590` | CAD AttachAssembly, IOCP/socket 10054. `transient_failure_max_retries` (default 2 → 3 attempts). watchdog.err는 고의로 제외 | 그 외 worker nonzero | `skip_existing_mesh` (default True) + `os.path.isfile(.msh.h5)` | **예**. hash/manifest/metrics 없음 | `.msh.h5`만 있고 `manifest.json` 없으면 skip되어 재메시 안 함. `rebuild_mesh_manifest.py:141-154`는 **기존** manifest 필요. Astra dead end 확인+악화 | CLEAR — skip을 msh+valid manifest+hash로. missing manifest는 remesh 강제. 279 전 |
| T4-02 | T4 | `batch_solver_sweep.py:46-58,203-217` | **없음** | missing mesh; worker nonzero | `skip_existing_final_data` (default True) + cas **and** dat exist | **예**. size/hash/stop_reason/quality 없음 | T2-20: RUNNING manifest + old finals → skip. finalize 후 write 실패+옛 파일 → skip | CLEAR — skip을 current manifest COMPLETE + artifact mtime/hash. 279 전 |
| T4-03 | T4 | `batch_solver_rerun.py:462-465,5416` | live Fluent 세션 안에서 strategy. 배치 수준 session retry 아님 | 후보 없음/Windows 아님 | `--skip-existing-rerun-success` = 이전 results CSV의 SUCCESS 행 | CSV 기록 존재. artifact 유효성 아님 | CSV SUCCESS인데 빈 dat면 skip | CLEAR — SUCCESS를 manifest+files로 |
| T4-04 | T4 | `batch_report_extract.py:428-443` | **없음** (retry는 `batch_postprocess` 쪽) | worker nonzero; layout resolve fail | `SKIP_EXISTING_REPORTS` + CSV 파일 + `LOAD_BEARING_SUMMARY_METRICS` 비공백 | 존재+비공백. layout/hash/freshness 없음 | 옛 10-cell CSV가 열만 채우면 Diamond 30-cell을 skip | CLEAR — skip에 layout.n_total/mesh_hash. 279 전 |
| T4-05 | T4 | `batch_postprocess_all_cases.py:950-1026,1133-1146` | report: socket reset, Scheme heap (새 세션). shear: fallback mode retry | Canonical CP/load-bearing 실패는 session signature 없으면 재시도 안 함 | `--skip-existing`: `summary_wide.is_file()` / contour files / shear files | **예**. extract 쪽 validation 없음 | extract가 artifact 쓴 뒤 gate fail → 파일 있음 → postprocess skip | CLEAR — skip을 extract와 같은 CSV validation+layout |
| T4-06 | T4 | `case_inventory.py:467-473,548-574` | 없음 | 기본: 불량 manifest abort | `--skip-unreadable-manifests` opt-in | unreadable skip | 기본은 fail-closed. `_iter_child_dirs` OSError는 트리를 숨김 (T2-17) | CLEAR — T2-17 |
| T4-07 | T4 | solver worker `solver_code` launch/write (A/C-02) | 없음 | calculate re-raise | sweep skip 위 | launch 실패: manifest 없는 run leaf. 재실행 가능(파일 없으면 skip 안 함) | 유예 가능(재실행 가능). 38 min crash checkpoint는 UNKNOWN | UNKNOWN — autosave 여부 live Fluent |

Dead-end 요약

1. mesh 파일 O, manifest X → skip_existing이 remesh 막고 rebuild도 거부. **메시 재생성만 회복.** 279 전.
2. solver finals O (옛/빈), skip_existing_final_data → 재실행 안 함.
3. extract wide CSV O (열만 유효), postprocess skip_existing → 재추출 안 함. batch_report_extract는 재실행 가능.
4. 솔버 checkpoint 없음이면 iterate crash는 전부 재실행. UNKNOWN.

## T5 — hygiene

| id | task | file:line | what | what breaks | triage |
|---|---|---|---|---|---|
| T5-01 | T5 | `src/ro/domain_layout.py:501` | `validate_layout_against_x_extent`는 `tests/test_domain_layout.py`만. scripts 호출 0 | T3-01. 테스트-only validator | CLEAR — live preflight |
| T5-02 | T5 | `src/ro/fluent_report_helpers.py:2490` | `derive_periodic_spacer_pressure_metrics` live 호출 0 (`_for_layout`만) | T1-03 | CLEAR — dead |
| T5-03 | T5 | `scripts/batch_report_extract.py:250-260` | `aggregate_output_paths` 본문이 두 번. 두 번째 unreachable | 없음 (동일 return) | CLEAR — 중복 삭제 |
| T5-04 | T5 | `configs/batch_post_config.py:36-45` | `geometries=["Sin_ST","Sin_SL"]`. `post_cases=[]`이면 `cases_from_run_manifests()`라 **미사용** | 직접 product generator를 켜면 archive geo로 279를 빗나감 | CLEAR — live list를 CAMPAIGN_GEO_IDS로. D-02 |
| T5-05 | T5 | `scripts/make_summary_figures.py:43-52` | `GEO_ORDER`가 `Empty`,`Diamond_Spacer`,`Sin_ST`… | 현재 `geo_id`와 불일치. 그림 정렬/필터 깨짐 | CLEAR |
| T5-06 | T5 | `docs/archive/meshing_code_260612.py:92` | `geo_name == "empty"` vs live id `REF_empty`. **live** `meshing_code_260616.py:333`은 `cfg.family == "empty"` (family 키, OK) | archive only. Astra 사례의 live 잔존은 family 비교로 수정됨 | CLEAR — archive. 추가 live `geo_id=="empty"` 없음 |
| T5-07 | T5 | `scripts/_tmp_cell_profile.py:41`, tests comments | `D2450_a45_7c_brg110`, `Sin_ST`, `mesh_max085_...` | `_tmp_*`는 비live. `test_batch_layout_wiring.py:217` 등 fixture | CLEAR — live leftover는 T5-04/T5-05 |
| T5-08 | T5 | `docs/AGENTS.md:151` `784 passed`; `docs/DEPLOY_RUNBOOK.md:54-60` `over 800`; `README.md:117` `over 800` vs T6 **1056 passed, 3 failed, 1 skipped** | 운영자가 green이라고 보고 279를 시작 | CLEAR — 문서를 현재 baseline에 |
| T5-09 | T5 | `docs/AGENTS.md` vs `configs/batch_config.py:297-373` | AGENTS는 31×9. live `mesh_batch_cases`는 Diamond 미포함 22; `solver_sweep_cases` 5개. D-02 | 잘못된 entrypoint | CLEAR — D-02. 뒤집지 말고 docs/list 분리 |
| T5-10 | T5 | `list_named_object_names` | `fluent_report_helpers.py:29`, `pyfluent_report_extract.py:423`, `solver_code_260616.py:617` 삼중 | fallback `[]` 동작이 세 곳. A-09 | CLEAR — helpers 한곳으로 |
| T5-11 | T5 | `src/ro/campaign_geo_ids.py:24` `LEGACY_ML_GEO_ID_PREFIX="M_r"` | 금지 토큰 가드. live id 아님 | leftover가 아니라 guard | CLEAR — 유지 |
| T5-12 | T5 | `CURRENT_LAYOUT` / `CELL_LENGTH_X_M` | tests + qoi tests fixture. production manifest path | T1-05 | CLEAR |

중복 계산: Cell 7 report 이름 생성은 helpers 한곳. LMH 식은 `lmh_metrics` + UDF + Fluent expression (A-02, 재검사 T3-14). `create_x_normal_plane`는 helpers가 정본, extract는 wrap.

## T6 — test suite

실행: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` (WSL).

결과: **1056 passed, 3 failed, 1 skipped**, 2.16–2.24 s. D-01과 동일.

| id | task | file:line | what | code vs expectation | triage |
|---|---|---|---|---|---|
| T6-01 | T6 | `tests/test_backfill_run_manifest_fields.py:45,78` | backfill이 D2450 `membrane_blocked_area_frac_geometric`을 registry `None`으로 채움. 테스트는 `0.0` | **테스트가 틀림.** `campaign_geometry.py:297` Diamond geometric=`None`. 0.0은 Pillar footprint 혼동 | CLEAR — expectation을 `None`으로. 코드 유지 |
| T6-02 | T6 | `tests/test_inventory_convergence_classification.py:216` | `len(CASE_INVENTORY_FIELDNAMES)==120`, 실제 **123** | **테스트가 틀림** (count). 추가분은 `pp_pressure_drop_rel_spread_window` / `_cells_4_7` / `_note` 등 quality 열 | CLEAR — 123으로 갱신하거나 required names set 검사 |
| T6-03 | T6 | `tests/test_campaign_geo_ids.py:114` | `RO_DATA_ROOT` 없으면 skip | skip 정당. data root 없음 | CLEAR |
| T6-04 | T6 | gate | `.github/` 없음. repo 스크립트가 full pytest를 안 돌림. `docs/DEPLOY_RUNBOOK.md:54`는 수동 `python -m pytest -q`. `pyproject.toml` testpaths만 | 부분 실행이 이 3 fails를 가림. runbook "over 800"도 실패를 놓침 | CLEAR — 279 전 수동 full suite를 실제 결과와 맞출 것. CI는 유예 |

## 279-run 전에 vs 유예 (최종)

**전에**

- T1-06/T1-07 Diamond `_COMMON_MESH` `n_active_cells=7` / `cell_length_x_m=0.003465`가 config-wins로 registry를 덮지 않게.
- T2-20 stop-reason 실패 후 final write + exit 0.
- T2-21 inlet TUI 실패 후 `Inlet BC set`.
- T2-17 inventory 트리 OSError → 빈 목록.
- T3-01 layout length vs measured x (live raise).
- T3-02 blocked `==0.0`.
- T3-03 `u_mean*G==u_target`.
- T4-01 mesh skip이 msh 존재만 봄 + missing-manifest dead end.
- T4-02/T4-05 skip이 파일 존재만.
- T4-04 extract skip이 layout/hash를 안 봄.
- T5-08/T6 docs·suite가 green이 아님 (3 fails는 expectation).
- T2-22 UDF가 `THREAD_NAME`을 쓰면 그 항도 전.

**유예**

- T1-01 continuity 4–7 열, T1-02 cell2/3, T1-05 상수 이름, T1-09 CLI default.
- T2 diagnostic excepts, T2-15 (`compute_cp_spread` off), T2-23 QoI monitor, T2-28 ledger.
- T5-03 unreachable duplicate, T5-05 figures GEO_ORDER (그림 단계).
- T6-01/T6-02 테스트 수정 (코드 계약은 이미 맞음) — 스위프를 막지 않음. 다만 green이라고 믿으면 안 됨.

**UNKNOWN**

- T1-08 30-cell Fluent report 한도/시간 (`C:/ro_data`·live Fluent).
- T2-08/T2-12 stale iso overwrite.
- T4-07 autosave/checkpoint.
- T3-16 CP 재구성 identity (aggregation).

---
