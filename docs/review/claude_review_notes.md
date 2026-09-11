# Claude repository review — mechanical first pass

## Progress

- 검토 기준 commit: `d453e0a29fa954e51655954dca3604cc8279885d`.
- T1 hardcoded layout assumptions: **COMPLETE** (batch 1 core layout/config, batch 2 extract/report helpers/UDF/inventory, batch 3 meshing/figure/pyensight).
- T2 success-permitting `except` clauses: **PARTIAL — campaign path 기준 COMPLETE.** batch 4/5/6에서 `pyfluent_report_extract.py`, `fluent_report_helpers.py`, `manifest.py`, `solver_common.py`, `solver_code_260616.py`, `case_inventory.py`, `batch_postprocess_all_cases.py`, `batch_report_extract.py`, `meshing_code_260616.py`, `batch_meshing.py`, `batch_solver_sweep.py`, `batch_solver_rerun.py` 완료. 중단 지점: figure/진단 전용 스크립트 약 350개 clause (`pyensight_contour_export.py` 129, `pyfluent_shear_contour_export.py` 68, `pyensight_extra_figures.py` 68, `diagnose_*` / `_probe_*` / `_tmp_*`). 근거는 T2 절 말미.
- T3 unchecked arithmetic identities: **COMPLETE** (미검증 10건 + 기존 검증 7건).
- T4 retry, skip and recovery: **COMPLETE** (6개 스테이지 요약, findings 6건, dead end 6건).
- T5 repository hygiene: **COMPLETE** (dead/test-only, legacy naming, docs 모순, 중복).
- T6 full test suite: **COMPLETE** (`3 failed, 1056 passed, 1 skipped`).

### 출력 파일에 대한 메모

요청문 본문은 `docs/review/claude_review_notes.md`를, working method와 output format 절은
`docs/review/sol_review_notes.md`를 지정했다. 처음에는 후자에 append했으나, 작업 중 **다른
세션이 같은 파일을 동시에 재작성**해 T2 후반·T3·T4·T5 섹션이 덮어써졌다. 그래서 완결본을 이
파일에 기록한다. `sol_review_notes.md`에는 그 세션의 T1/T2와, 이 파일과 동일한 T6·우선순위
목록이 남아 있다. 두 파일의 finding id는 서로 독립이다. `astra_review_notes.md`는 수정하지 않았다.

### 범위 제한

- `C:/ro_data`, Windows workstation, live Fluent, solver run에 접근하지 않았다. 실제 manifest 내용, mesh 파일, run 결과, timing은 **UNKNOWN**이다.
- source / config / test 파일을 변경하지 않았다. test suite는 읽기 전용으로 1회 실행했다.
- 과학적·수치적 타당성(CP 정의, boundary-layer 해상도, 이산화 오차, 난류, 안정성)은 판단하지 않고 NEEDS-ASTRA로만 기록했다.
- 측정 근거가 있는 결정(`membrane_blocked_area_frac=0.0`, flux `(without-sources)`, `periodic_after_surface_mesh=True`, meshing→`switch_to_solver()`, `u_mean_ms=u_target/G`, `n_lead_excluded=3`)은 뒤집지 않았다.
- `astra_review_notes.md`의 완료된 A1 duplicated source-of-truth inventory는 재검토하지 않았다.

## Triage tag

- **CLEAR**: 코드만으로 동작과 최소 수정이 명확함.
- **NEEDS-ASTRA**: physics, numerics, Fluent semantics 또는 깊은 cross-file 판단이 필요함.
- **UNKNOWN**: `C:/ro_data`, live Fluent 또는 solver run 없이는 판정 불가함.

---

## T1 — hardcoded layout assumptions

### Batch 1 — core layout / config

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C1-01 | T1 | `src/ro/convergence_quality.py:33,88-92,131-138` | `PRESSURE_DROP_CELLS=(4,5,6,7)`는 과거 continuity 열이고, 채점은 인자로 받은 `evaluation_cell_numbers`를 쓴다. | 30-cell에서 crash는 없다. 하지만 `n_active=5`(D2450_a60, active cell 2–6)에서는 `pp_pressure_drop_cell_7`이 존재하지 않아 열이 **조용히 `None`**이 되고, `n_active=27`에서는 cell 4–7이 전부 lead 배제 구간이라 값은 있으나 무의미하다. 요청문의 calibration 사례이며 window 채점 경로는 이미 수정돼 있다. | **CLEAR** — legacy 열로만 유지하고 campaign 판정에는 `pp_pressure_drop_rel_spread_window`만 사용. |
| C1-02 | T1 | `src/ro/domain_layout.py:53-55,290-313` | `CELL_LENGTH_X_M=0.003465`, `BUFFER_LENGTH_IN_M`, `BUFFER_LENGTH_OUT_M`; `CURRENT_LAYOUT(n_active=7)`, `LEGACY_LAYOUT(n_active=3)`. | live import 없음(tests만 참조) → 30-cell run에 영향 없음. `CURRENT_LAYOUT`이라는 이름이 generic해 향후 오용 위험만 있다. | **CLEAR** — geometry-specific 이름으로 바꾸거나 test fixture로 이동. 279 전 필수 아님. |
| C1-03 | T1 | `configs/batch_config.py:132-155`, `:297-373`, `:488-498` | `_COMMON_MESH`에 `n_active_cells=7`, `cell_length_x_m=0.003465`, `periodic_shift_y=3.465`. Diamond별 `_DIAMOND_MESH_LAYOUTS`는 "Reference data, not used by this config"로 존재하며 `mesh_batch_cases`에 append되지 않는다. | 현재 22개 case에는 맞다. 그러나 같은 copy pattern으로 279-run list를 만들면 Diamond도 7-cell·3.465 mm pitch로 생성된다. crash가 아니라 **잘못된 domain**이 더 위험하다. Diamond 실제 값은 `n_active_cells` 5–27, pitch 0.943–4.900 mm. | **CLEAR — 279 전 필수.** |
| C1-04 | T1 | `configs/run_config.py:327-329` | legacy `domain_length_m=0.017325`, `buffer_length_m=0.003465`가 남아 있고 두 값끼리만 검증된다. | solver live report path는 mesh-manifest layout을 쓰므로 직접 오염은 없다. 다만 validator가 실제 layout과 무관한 5-cell-era 값을 정상으로 인증한다. | **CLEAR** — 제거하거나 resolved layout에서 생성. |

### Batch 2 — extract / report helpers / production UDF / inventory

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C1-05 | T1 | `udfs/260822_RO_UDF.c:1115-1116,1224-1236` | `INLET_G_MIN=0.9999` / `INLET_G_MAX=1.02`. band는 D2450 측정 excess(+0.308%..+0.3742%)의 약 5배로 잡은 값. 벗어나면 `G=1.0` fallback + `RO_UDF_INLET_G_OUT_OF_RANGE` warning이고 성공 marker `RO_UDF_INLET_PROFILE_G=`는 **출력되지 않는다**. | Diamond는 `m_max`가 다르고(`D0817_a60`은 0.060) spanwise pitch가 0.943–4.900 mm로 변해 inlet z-quadrature와 face 수가 달라진다. G가 band를 벗어나면 run은 틀린 값이 아니라 C1-06 gate에서 **정지**한다. | **NEEDS-ASTRA** — band 1.02가 Diamond 전 범위를 덮는지, geometry별 band 또는 measured-G 기반 판정이 필요한지. |
| C1-06 | T1 | `scripts/solver_code_260616.py:871-894`, `:896-928` | `agreed_inlet_profile_g` / `wait_for_agreed_inlet_profile_g`가 marker 없으면 30 s 후 `RuntimeError`. | C1-05의 fallback에서 **fail-closed**. G=1 오염이 조용히 통과하지 않는다. | **CLEAR** — 수정 불필요. C1-05 결정 시 이 gate를 약화시키지 말 것. |
| C1-07 | T1 | `udfs/260822_RO_UDF.c:1101-1103,1268-1270` | `INLET_AREA_EXPECTED_M2 = 2.668e-6` (W 3.465 mm × H 0.770 mm). | 주석과 출력 문구에 "D2450_a45-only reference; a mismatch on other geometries is expected"가 명시돼 있고 print에만 쓰인다. gate 아님. | **CLEAR** — 조치 불필요. 로그 오독만 주의. |
| C1-08 | T1 | `scripts/pyfluent_report_extract.py:1127-1180`, `src/ro/fluent_report_helpers.py:874-902` | boundary report 생성이 `range(n_unit_cells + 1)`로 layout에서 파생. boundary당 4 report. | 인덱스 하드코딩 없음. 다만 definition 수가 boundary 11개(≈135)에서 31개(≈**215**)로 증가. Astra C-05의 "135 definitions = 736 s"를 선형 적용하면 report 생성만 **약 1170 s**. | **UNKNOWN** — 실제 스케일링은 live Fluent 필요. 279-run 일정 추정에 반영할 것. |
| C1-09 | T1 | `src/ro/fluent_report_helpers.py:2413-2450` vs `:2452-2486` | `derive_spacer_cell_metrics`(대칭 legacy, `spacer_cell_numbers(n_unit_cells, n_buffer_cells_each_end)`)와 `derive_spacer_cell_metrics_for_layout`(asymmetric)이 같은 계산을 두 번 구현. | legacy는 `n_buffer_cells_each_end`를 요구하는데 asymmetric layout에서 그 값은 `None`(`layout_post_config_values`). 현재 legacy는 test-only이나 부활하면 Diamond에서 잘못된 cell 집합 또는 `TypeError`. | **CLEAR** — asymmetric variant만 유지(C5-24와 동일). |
| C1-10 | T1 | `src/ro/fluent_report_helpers.py:2530-2541`, `:2567-2579` | `pp_pressure_drop_cell2_over_cell3`가 global cell 2/3에 고정 (주석: "Intentionally fixed to cells 2 and 3"). | 의도된 entrance-contamination 진단. Diamond 전 범위에서 cell 2/3은 항상 active_1/active_2이므로 정의는 유지된다. | **NEEDS-ASTRA** — pitch가 0.943–4.900 mm로 변할 때 family 간 비교 가능한 양인가. |
| C1-11 | T1 | `scripts/case_inventory.py:2052-2057`, `scripts/batch_postprocess_all_cases.py:1423-1429` | 고정 `fieldnames` + `extrasaction="ignore"`. | per-cell 열(`pp_pressure_drop_cell_9..28` 등)이 crash 없이 **조용히 버려진다**. 원본 `summary_metrics_wide.csv`는 동적 컬럼이라 보존된다. | **CLEAR** — 의도면 문서화, 아니면 layout에서 생성. 279 전 필수 아님. |
| C1-12 | T1 | `scripts/pyfluent_report_extract.py:2318-2333` | `summary_metrics_wide`는 `summary_rows_to_wide_record`에서 동적 생성 후 정렬. | 고정 컬럼 없음. 30-cell에서 열 수만 증가. | **CLEAR** — 조치 불필요. |

### Batch 3 — meshing / figure / pyensight

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C1-13 | T1 | `scripts/meshing_code_260616.py:45-88`, `:110` | mesh manifest의 `n_active_cells`, `cell_length_x_m`, `buffer_length_in_m/out_m`, `periodic_shift_y_m`는 **config에서 그대로 복사**된다. 실제 domain은 `geometry_dir(family, geo_id)/{geo_id}.dsco` CAD가 결정하며 둘을 대조하는 코드가 없다. | **T1 전체에서 가장 위험한 지점.** Diamond가 `_COMMON_MESH`의 `n_active_cells=7`을 상속하는데 실제 CAD는 5–27 cell이다. mismatch 시 crash가 아니라 **모든 unit-cell boundary plane이 잘못된 x에 생성**되고 dP/CP/c_b window 전부가 조용히 틀린다. 지금까지 모든 mesh가 7-cell이라 드러나지 않았다. | **CLEAR — 279 전 필수, 최우선.** |
| C1-14 | T1/T3 | `src/ro/mesh_common.py:376-390,484-515` vs `scripts/meshing_code_260616.py:45-88` | `parse_mesh_metrics_text`가 `domain_extent_x_m/y_m/z_m`를 파싱하지만 `build_mesh_manifest_payload`는 `ortho_min`, `AR_max`, `skewness_max`, `skewed_face_fraction`, `cell_count`, `porosity`만 가져간다. **측정된 extent는 버려진다.** | C1-13의 gate를 나중에 추가해도 기존 mesh에는 측정값이 없어 재meshing 없이 검증할 수 없다. `manifest_validation.py:47-49`가 `domain_extent_*_m`을 range-check 대상으로 등록해 둔 것은 필드가 있어야 한다는 의도를 보여주지만 writer가 없다. | **CLEAR — 279 전 필수** (Diamond mesh 생성 전에 넣어야 소급 재meshing이 없다). |
| C1-15 | T1/T5 | `src/ro/domain_layout.py:470-499`, `:501-532` | `parse_solver_log_domain_extents_m`과 `validate_layout_against_x_extent`는 **`tests/test_domain_layout.py`에서만 호출**된다 (`scripts/`, `src/`, `configs/`에 caller 없음). | Astra A-04의 "live caller 유무 UNKNOWN"을 확정. layout arithmetic gate는 구현돼 있으나 어떤 live path에도 연결돼 있지 않다. 게다가 validator는 결과 객체만 반환하고 raise하지 않으므로 호출자가 `ok`를 검사해야 한다. | **CLEAR — 279 전 필수** — meshing gate와 solver preflight에 연결. |
| C1-16 | T1 | `scripts/pyensight_contour_export.py:3446-3507`, 특히 `:3483` | window aggregate k를 `cell_numbers = sorted(set(k_by_cell) & set(area_by_cell))`로 구하고, 이 집합이 `evaluation_cell_numbers(layout)`와 같은지 확인하지 않는다. | 부분 집합으로 area-weighted k를 만들어도 통과한다. Diamond에서 window cell이 2(D2450_a60)에서 24(D0817_a30)로 변하므로 부분 집합이 훨씬 눈에 띄지 않는다. | **CLEAR** — 교집합이 window와 정확히 일치하지 않으면 fail. 그림을 비교에 쓰기 전 필수. |
| C1-17 | T1 | `scripts/make_summary_figures.py`, `scripts/pyensight_extra_figures.py` 전체 | cell 개수/인덱스 하드코딩 없음. 집계 컬럼만 소비. | 30-cell에서 문제 없음. | **CLEAR** — 조치 불필요. |
| C1-18 | T1 | `scripts/_tmp_probe_reduction.py:51-62,218-220` | `N_SPACER_CELLS=7`, `CELL_LENGTH_M=0.003465`, `DOMAIN_X_MAX_M=0.03465`, `SPACER_4_X_MIN_M/MAX_M`, docstring "11 planes ... 34.65 mm". | `_tmp_` probe script로 campaign path가 아니다. Diamond에 그대로 돌리면 조용히 틀린 평면을 만든다. 대조군 `scripts/_tmp_cell_profile.py:63-74`는 같은 값을 `layout_from_mesh_manifest`에서 받아온다. | **CLEAR** — 삭제 또는 manifest 기반 전환. campaign 차단 요인 아님. |

**T1 결론:** 실제 핵심은 magic index가 아니라 **layout 값이 config 선언일 뿐 측정과 대조되지 않는다는 것**이다 (C1-13/14/15). Diamond는 config 기본값과 CAD가 처음으로 어긋나는 family이므로 이 셋이 279-run 전 최우선이다. extract/report 생성 경로 자체는 layout-driven이며 30-cell index crash를 만드는 loop는 찾지 못했다.

---

## T2 — process가 성공으로 끝날 수 있는 `except` 절

분류: **FATAL-CORRECT** re-raise 또는 nonzero exit / **SILENT-BLANK** 출력이 None·빈 값·default가 되고 이를 거부하는 gate 없음 / **SILENT-STALE** 이전 값이 살아남아 현재 값처럼 보임 / **FALLBACK-OK** diagnostic 전용 degradation.

### Batch 4 — extract / report helpers / manifest / solver_common

| id | task | file:line | 도달하는 값 | 분류 / 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C2-01 | T2 | `scripts/pyfluent_report_extract.py:1300-1310` | 실패 report는 `computed_values[name]=None`, `raw_results[name]={"error":...}` | **FATAL-CORRECT** — `FLUX_BOUNDARY_MASSFLOW_REPORTS` 또는 `LOAD_BEARING_REPORT_NAMES`면 즉시 `LoadBearingReportComputeError` re-raise. `:1312`의 `require_load_bearing_report_computes`가 2차 gate. **요청문의 calibration(Cell 8이 mass-closure 열에 None을 쓰고 exit 0)은 이 commit에서 이미 수정됐다.** | 조치 불필요. Astra A-07/08/09 확장 시 미수정으로 계산하지 말 것. |
| C2-02 | T2 | `:1226-1228`, `:1250-1252` | 생성 실패 report는 `failed_report_specs`에 기록, warning | **FATAL-CORRECT** — `:1261`의 `require_load_bearing_report_definitions`가 load-bearing 누락 시 raise. | 조치 불필요. |
| C2-03 | T2 | `:1417-1422`, `:1495-1507`, `:1659-1672`, `:1674-1682` | mid-plane iso-surface 실패 / 모든 salt-field 후보 실패 / segmented CP 실패 / `cp_canon_window_avg` 누락 | **FATAL-CORRECT** — 네 경로 모두 "cp_inlet_avg (L2) is not a substitute for canonical CP" 메시지와 함께 `RuntimeError`. canonical CP 경로는 fail-closed. | 조치 불필요. |
| C2-04 | T2 | `:2456-2460` | run manifest의 `convergence_quality`가 기록되지 않음; CSV/stdout에는 존재 | **SILENT-STALE** — write 실패 시 이전 run의 quality 값이 manifest에 남아 최신처럼 보인다. rerun 후 manifest만 읽는 소비자는 이전 판정을 본다. exit 0. | **CLEAR — 279 전 필수** (rerun이 반복되는 경로). |
| C2-05 | T2 | `:433-434`, `:442-443`, `:451-453`, `:478-479`, `:500-501`; `src/ro/fluent_report_helpers.py:35-36,44-45,53-55` | zone/named-object 목록이 `[]`로 축약 | **SILENT-BLANK → 실질 FATAL** — 빈 목록으로 surface report를 만들면 `:585-586`이 `ValueError: Cannot create ...: no surfaces.`를 던지고 C2-02 gate에 걸린다. 다만 "접근 실패"와 "실제로 빈 zone list"가 구분되지 않아 오류 원인이 가려진다. Astra A-09의 caller별 확인을 이 경로에 한해 완료. | **CLEAR** — 진단 품질 문제. campaign 차단 아님. |
| C2-06 | T2 | `:1360-1370` | salt mass-fraction range 진단이 `*_error_type/_message/_combined` 열로 기록, 값 없음 | **FALLBACK-OK** — 실패가 출력 열에 명시되고 canonical 경로와 무관. | 조치 불필요. |
| C2-07 | T2 | `:1593-1600` | legacy whole-domain center-plane 평균 → `_c_bulk_center_diag`에 사유 기록 | **FALLBACK-OK** — canonical c_b는 이미 계산된 뒤이며 inventory 전용. | 조치 불필요. |
| C2-08 | T2 | `:2497-2503` (+ `:2508-2538` finally) | 본체 예외는 `raise` → nonzero. finally의 timing write / transcript stop / session exit / chdir 실패는 warning | **FATAL-CORRECT**(본체) + **FALLBACK-OK**(cleanup). cleanup 실패는 결과 신뢰성이 아니라 자원 회수 문제 (Astra A-07 대조와 동일). | 조치 불필요. |
| C2-09 | T2 | `src/ro/fluent_report_helpers.py:968-994` 및 `audit_load_bearing_summary_columns` | — | **gate 부재** — docstring은 "Checked after writing artifacts"라 하지만 live caller는 `scripts/validate_load_bearing_summary_csvs.py`(사후 도구)와 tests뿐이다. extract는 wide CSV의 blank load-bearing 열을 스스로 검사하지 않는다. | **CLEAR — 279 전 필수** — extract 말미에서 `load_bearing_summary_missing_columns(wide_record)`가 비어있지 않으면 nonzero 종료. |
| C2-10 | T2 | `src/ro/fluent_report_helpers.py:71-72,82-84,99-103,120-121,131-133,148-152` | iso-surface: settings API 실패 → TUI fallback, 둘 다 실패 시 `RuntimeError` | **FATAL-CORRECT** — 두 오류를 모두 메시지에 담는다. | 조치 불필요. |
| C2-11 | T2 | `src/ro/manifest.py:499-500,523-524,534-535,582-583` | 모두 `ManifestError`로 변환되어 전파 | **FATAL-CORRECT**. `_write_json`은 temp + `os.replace` + `fsync`로 atomic. | 조치 불필요. |
| C2-12 | T2 | `src/ro/manifest.py:821-825` | `_iter_child_dirs`가 `OSError`를 빈 child list로 변환 | **SILENT-BLANK** — 접근 실패한 tree가 "run 없음"과 구분되지 않는다. Astra A-09 재확인. | **NEEDS-ASTRA** — 모든 caller에서 "0건"이 정상 종료로 이어지는지 cross-file 추적 필요. |
| C2-13 | T2 | `src/ro/solver_common.py:210-217`, `:238-245` | `max_iterations` / `residual_target`가 파싱 불가면 warning 후 **default로 대체** | **SILENT-BLANK(default 대체)** — 279-run sweep에서 config 오타가 조용히 다른 iteration cap / residual target으로 279개 run 전부를 돌릴 수 있다. warning은 stdout에만 남고 manifest에는 fallback 값이 기록된다. | **CLEAR — 279 전 필수** — production 경로에서는 raise. |
| C2-14 | T2 | `src/ro/solver_common.py:304-308` | 파일 크기 읽기 실패를 `failures` 리스트에 축적 | **FATAL-CORRECT 계열** — 실패가 반환값으로 호출자 gate에 전달된다. | 조치 불필요. |
| C2-15 | T2 | `src/ro/solver_common.py:392-395,400-404,697-701,719-722,767-770` | transcript/report 파싱 중 비수치 토큰은 `continue`/`None` | **FALLBACK-OK** — 헤더·구분선 스킵을 위한 정상 파서 동작. | 조치 불필요. |
| C2-16 | T2 | `src/ro/solver_common.py:388-407` | `latest.update(row_values)`가 유효 row마다 누적 → 마지막 유효 값 유지 | **SILENT-STALE** — transcript 끝부분이 손상/절단되면 더 이른 iteration의 residual이 "final"로 보고된다. | **NEEDS-ASTRA** — 보고되는 residual에 iteration 번호가 동반돼 절단을 검출할 수 있는지 추적 필요. |

### Batch 5 — solver / inventory / postprocess / report driver

| id | task | file:line | 도달하는 값 | 분류 / 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C2-17 | T2 | `scripts/solver_code_260616.py:3668-3689`, `:3716-3729`, `:3732-3739` | stop reason 판정 실패 → warning, `solver_stop_reason`은 `None` → `finalize_worker_run_manifest` **건너뜀** → 그런데 `:3746`의 `write_case_data`는 실행되고 exit 0 | **SILENT-STALE** — final `.cas.h5`/`.dat.h5`는 존재하는데 run manifest는 `RUNNING`으로 남는다. 그리고 `batch_solver_sweep`의 skip은 **파일 쌍 존재만** 보므로(C4-02) 이 run은 이후 영구히 "완료"로 취급된다. Astra A-07의 downstream 미확인 부분을 확정. | **CLEAR — 279 전 필수** — 명시적 `stop_reason="unknown"` + FAILED를 manifest에 쓰고 nonzero 종료. |
| C2-18 | T2 | `:2181-2341`, `:2378-2509`, `:2537-2541`, `:2583-2591`, `:3533-3550` | 모든 under-relaxation 적용 실패는 `WARN_APPLY_URF_FAILED` outcome dict로 수집되어 **stdout에만 출력**. `relaxation_result`는 `:3550`에서 print될 뿐 manifest/CSV에 기록되지 않는다. | **SILENT-BLANK** — URF 적용 실패 시 Fluent 기본 relaxation으로 계속 solve하고 exit 0. 279-run 중 일부가 다른 numerics로 수렴해도 결과 파일에 흔적이 없다. re-run 재현성도 보장되지 않는다. | **CLEAR — 279 전 필수** — `applied` 전체를 manifest에 기록하고 `APPLIED_CONFIRMED`가 아닌 항목이 있으면 abort. |
| C2-19 | T2 | `:1717-1728` | solve-time spacer pressure monitor 생성 실패 → "continuing solver run" warning 후 부분 `created` 반환 | **SILENT-BLANK** — 최종 CSV는 별도 extract에서 나오므로 결과값 자체는 오염되지 않지만, 수렴 판정 근거가 조용히 달라진다. | **NEEDS-ASTRA** — monitor 부재가 QoI stop 판정을 바꾸는가. |
| C2-20 | T2 | `:1606-1611` | `defer_failures=True`면 QoI report 생성 실패를 미루고, 재시도 실패 시 raise | **FATAL-CORRECT**. | 조치 불필요. |
| C2-21 | T2 | `:528-546`, `:617-637`, `:662-663`, `:688-689`, `:708-719`, `:1281-1308`, `:1328-1330`, `:1407-1489`, `:1854-1862`, `:1889-1902`, `:1932-1937`, `:1959-1960`, `:991-1204` | 상태 읽기/출력 실패, TUI·scheme_eval 3단 fallback, report-file 속성 설정 실패 | **FALLBACK-OK** — 전부 print/조회용. 단 `:528-546`의 `update-solver-thread-names` 3단 fallback이 모두 실패하면 zone 이름이 갱신되지 않은 채 진행하며, 이후 `no surfaces` 오류로 간접 검출된다(C2-05과 동일 구조). | 조치 불필요. |
| C2-22 | T2 | `:1990-1992` | QoI report 파일 읽기 실패 → `(False, False, None)` | **SILENT-BLANK** — "아직 수렴 아님"으로 해석되어 max_iter까지 돈다. 결과는 틀리지 않으나 `stop_reason`이 실제와 다르게 기록된다. | **CLEAR** — 읽기 실패와 미수렴을 구분. 279 전 필수 아님. |
| C2-23 | T2 | `:113-114` | case-local UDF 없으면 master UDF로 `analytic_cwall` fallback | **SILENT-STALE** — 둘이 다르면 manifest에 master 값이 기록된다. Astra A1의 `analytic_cwall` 항목과 동일. | **NEEDS-ASTRA** — compiled library와 source의 동일성 보증이 없는 상태에서 이 fallback을 어디까지 허용할지. |
| C2-24 | T2 | `scripts/batch_report_extract.py:610-639` | 드라이버가 `FAILED` / `FAILED_METRIC_VALIDATION` 건수를 출력하고 **`sys.exit(1)` 없이 종료** | **SILENT-BLANK(드라이버 레벨)** — 279 case 중 일부가 실패해도 batch는 exit 0. 대조: `batch_solver_sweep.py:274-275`는 `if failures: sys.exit(1)`. | **CLEAR — 279 전 필수.** |
| C2-25 | T2 | `:33-35`, `:71-75`, `:406-412` | layout 해석 실패 → `(None, error)` → case가 `FAILED`로 기록되고 계속 | **FATAL-CORRECT(case 단위)** — layout 미상은 `LAYOUT_UNKNOWN`이 아니라 명시적 실패. 종료 코드는 C2-24 문제를 그대로 받는다. | C2-24와 함께 처리. |
| C2-26 | T2 | `:129-130`, `:524-525`, `:564-565`, `:607-608` | CSV 읽기/쓰기 실패 | `:129-130`은 **FATAL-CORRECT**(validation False). `:524-525`, `:607-608`(status CSV / merged summary 저장 실패)은 **SILENT-BLANK** — 집계 산출물이 없는데 배치는 성공으로 끝난다. `:564-565`는 개별 case를 merge에서 `continue`로 **조용히 누락**시킨다. | **CLEAR** — 집계 write 실패와 merge 누락을 실패 건수에 포함. |
| C2-27 | T2 | `scripts/case_inventory.py:413-418` | `batch_config` 로드 실패 → `_DEFAULT_MAX_ITER_FALLBACK` 대체 | **SILENT-BLANK(default 대체)** — `hit_max_iter_target` 판정 기준이 실제 campaign 설정과 달라질 수 있다. warning은 stderr에만. | **CLEAR** — 사용된 max_iter의 출처를 열로 기록. |
| C2-28 | T2 | `:560-566` | 읽을 수 없는 manifest를 `skipped_manifests`에 모으고 warning 후 skip | **SILENT-BLANK** — 279개 중 manifest가 깨진 run이 inventory에서 조용히 빠진다. Astra A-03의 required-field 추가 시나리오와 결합하면 대량 누락이 가능하다. | **CLEAR — 279 전 필수** (coverage 검증의 기반). |
| C2-29 | T2 | `:736-737`, `:744-745`, `:762-763`, `:803-804`, `:1234-1239` | 크기 0.0, binary True, 빈 텍스트 + error 문자열, `None`, 오류 문자열 | **FALLBACK-OK** — 모두 오류 사유를 반환값에 담아 호출자가 열로 기록. | 조치 불필요. |
| C2-30 | T2 | `scripts/batch_postprocess_all_cases.py:309-310,319-323,335-336,1042-1052` | `([], error)`, `({}, error)`, `None`, `STATUS_FAILED` plan | **FATAL-CORRECT(case 단위)** — 오류가 status로 승격. `:1653-1655`의 top-level은 `return 2`. | 조치 불필요. |
| C2-31 | T2 | `:943-946` | retry 판정용 로그 읽기 `OSError` → `pass` → 텍스트 없이 분류 | **SILENT-BLANK** — `classify_retryable_report_failure`가 `None`을 반환해 **transient crash가 재시도되지 않고** permanent로 오분류된다. | **CLEAR** — 로그 읽기 실패를 retryable로 처리하거나 경고로 승격. |

### Batch 6 — meshing worker / batch 드라이버 / rerun

| id | task | file:line | 도달하는 값 | 분류 / 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C2-32 | T2 | `scripts/meshing_code_260616.py:1150-1154` | 본체 예외는 `run_error` 기록 후 `raise` | **FATAL-CORRECT**. | 조치 불필요. |
| C2-33 | T2 | `:1174-1202` | mesh run record write 실패 → warning만 | **SILENT-BLANK** — `mesh_run_record.json`은 manifest 부재 시 유일한 provenance 후보인데(C4-07) 이것마저 조용히 사라질 수 있다. | **CLEAR** — write 실패를 nonzero로 승격. |
| C2-34 | T2 | `:171-208` | `teardown_meshing_session`이 graceful→force→wait 3단계, 최종 실패 시 `"unresolved"` 반환 | **FALLBACK-OK** — 반환값이 상태를 담는다. 다만 호출자가 이를 사용하는지 확인 필요. | **CLEAR** — 반환값을 mesh run record에 기록. |
| C2-35 | T2 | `:143-147`, `:556-562`, `:591-597`, `:1160-1172`, `:1207-1210` | 로그 읽기 `""`, label/task 조회 실패 warning, transcript cleanup `pass` | **FALLBACK-OK** — 진단·정리 경로. `:143-147`의 빈 문자열이 quality gate 파싱에 쓰이면 `fail_if_quality_not_parsed`가 처리한다. | 조치 불필요. |
| C2-36 | T2 | `scripts/batch_meshing.py:175-178` | retry 매칭용 로그 읽기 실패 시 `pass` | **SILENT-BLANK** — C2-31과 동일 패턴. transient CAD/socket 실패가 재시도되지 않고 permanent로 기록된다. | **CLEAR** — C2-31과 함께 처리. |
| C2-37 | T2 | `:235-239` | leftover Fluent/Discovery/CADReaders 프로세스 목록 조회 실패 → warning + `[]` | **SILENT-BLANK** — 남은 프로세스가 있어도 "없음"으로 보고된다. 다음 case가 socket 경합에 그대로 들어가 279-run에서 연쇄 실패의 원인이 될 수 있다. | **CLEAR** — 조회 실패 시 보수적으로 추가 대기하고 상태 기록. |
| C2-38 | T2 | `scripts/batch_solver_rerun.py:1227-1243` | 이전 결과 CSV 읽기 실패 → warning + 빈 skip set | **FALLBACK-OK** — 안전한 방향(전부 재실행)으로 degrade. | 조치 불필요. |
| C2-39 | T2 | `scripts/batch_solver_rerun.py:1520-1978`, `:2661-` (87개 중 대부분) | `solver_code_260616.py`와 동일한 `WARN_APPLY_URF_FAILED` 패턴, best-effort setattr chain(`:1929-1946`), errors 리스트 누적 | **SILENT-BLANK** — C2-18과 같은 구조가 rerun에도 복제돼 있다. `apply_pseudo_transient`는 지원 경로를 못 찾으면 `"No supported pseudo-transient setting path was found."`만 출력하고 계속한다. | **CLEAR** — C2-18과 같은 수정을 적용. rerun은 279 필수 경로가 아니므로 우선순위는 그 다음. |
| C2-40 | T2 | `:6062`, `:6079`, `:6149` | top-level은 `return 2` / `return 1` | **FATAL-CORRECT**. | 조치 불필요. |

### T2 미착수 범위와 그 근거

`pyensight_contour_export.py`(129), `pyfluent_shear_contour_export.py`(68), `pyensight_extra_figures.py`(68), `diagnose_yi_saturation.py`(29), `_tmp_probe_reduction.py`(29), `probe_cff_surface_areaavg.py`(19), `diagnose_cp_quantile_spread.py`(15), `diagnose_cp_face_filters.py`(15), `pyfluent_field_check.py`(13), `_tmp_cell_profile.py`(10), `_probe_*`(24), 기타 진단 스크립트의 `except`는 분류하지 않았다. 이들은 그림 생성과 진단 전용으로 279-run campaign gate 밖이며 산출물은 비교용 figure다. **그림을 논문 비교에 쓰기 전에는 별도 pass가 필요하다** — `pyensight_contour_export.py`는 C1-16(window cell 부분 교집합)과 Astra A1의 stale-file 항목을 이미 갖고 있어 silent-blank가 그림에 그대로 나타날 수 있다.

**T2 결론:** 결과값 경로(canonical CP, load-bearing report)는 **이미 fail-closed로 강화**되어 있고 요청문의 calibration 사례는 현재 commit에 존재하지 않는다. 남은 silent success는 **프로세스/메타데이터 레벨**에 몰려 있다 — C2-17, C2-18, C2-24, C2-28.

---

## T3 — 데이터에 존재하지만 검증되지 않는 산술 항등식

기준: 두 값이 모두 이미 코드/manifest/CSV에 존재하고 하나가 다른 하나로부터 계산 가능한데 비교가 없는 경우만.

| id | task | 항등식 | 두 값의 위치 | 과거 어떤 bug를 잡았을까 / 무엇을 막는가 | 검증이 있어야 할 곳 | triage |
|---|---|---|---|---|---|---|
| C3-01 | T3 | `buffer_length_in_m + n_active_cells * cell_length_x_m + buffer_length_out_m == domain_extent_x_m` | 좌변: mesh manifest(config 복사). 우변: `mesh_common.parse_mesh_metrics_text`가 파싱하나 **manifest에 저장되지 않음**(C1-14) | Diamond가 `n_active_cells=7`을 상속한 채 5–27 cell CAD로 meshing되는 상황을 **유일하게** 잡을 수 있다. 과거에 잡은 bug는 없으나 모든 mesh가 7-cell이라 발화하지 않았을 뿐이다. 요청문의 `cell_length_x_m*(n_buffer_in+n_active+n_buffer_out)` 형태는 buffer가 pitch의 정수배일 때만 성립하므로 쓰면 안 된다(D0817_a45는 buffer_in이 3 pitch, D2450_a60은 0.707 pitch). | `meshing_code_260616.py:1143` manifest write 직전 + solver preflight. `validate_layout_against_x_extent`가 이미 정확한 좌변(`total_length_m`)을 구현하고 있어 **호출만 연결하면 된다**. | **CLEAR — 279 전 필수, 최우선** |
| C3-02 | T3 | `pp_area_mem == pp_udm_area_sum` | 둘 다 `LOAD_BEARING_SUMMARY_METRICS`(`fluent_report_helpers.py:816-817`), `pyfluent_report_extract.py:2079-2080`에서 나란히 CSV 기록 | Fluent surface-area(면 기반)와 UDM-11 accumulator(cell 기반 volume-sum)는 같은 막 면적을 두 경로로 측정한 값이다. **불일치는 face→cell UDM 누적이 깨졌다는 직접 증거**이며 canonical CP 전체가 이 누적 위에 있다. Astra B-03의 "face → cell UDM → surface interpolation 동등성 미검증"에 대한 가장 값싼 실증 검사. | extract Cell 8 이후, `assert_midplane_c_b_matches_boundary_mixing_cup`과 같은 위치·방식으로 rel_tol gate. | **CLEAR — 279 전 필수** (tolerance는 NEEDS-ASTRA) |
| C3-03 | T3 | `pp_pressure_drop_spacer == Σ(pp_pressure_drop_cell_N)` (N = active cells) | 좌변 `pyfluent_report_extract.py:1113-1119`, 우변 `derive_spacer_cell_metrics_for_layout` | 같은 span을 서로 다른 plane 집합으로 잰 값이다. 불일치는 unit-cell boundary plane이 spacer edge plane과 다른 x에 놓였다는 뜻 — **C3-01이 놓친 layout 오류가 여기서 드러난다.** | derived metrics 계산 직후. | **CLEAR — 279 전 필수** |
| C3-04 | T3 | mesh manifest `membrane_blocked_area_frac == 0.0` (campaign policy) | `manifest_validation.py:562-567`은 `[0, 1)` 범위만 검사 | 정책은 정확히 `0.0`인데 schema는 `0.9`도 통과시킨다. schema를 정책으로 바꾸면 안 되므로(재사용 가능해야 함) **별도 campaign preflight**가 필요하다. | schema가 아니라 279-run 시작 전 campaign gate. | **CLEAR — 279 전 필수** |
| C3-05 | T3 | mesh manifest `membrane_blocked_area_frac == run manifest 동명 필드` | solver는 mesh 쪽(`solver_code_260616.py:3154-3155`), extract는 run 쪽(`pyfluent_report_extract.py:1017-1018`) | 같은 물리량을 두 manifest에서 독립적으로 읽는데 동일성 검사가 없다. **run 쪽에는 `[0,1)` 범위 검사조차 없다**(검사는 mesh 전용). solver와 extract가 다른 blocked fraction으로 LMH를 계산할 수 있다. 현재 registry가 0이라 fresh manifest는 우연히 일치. | extract preflight(`:1017` 직후)에서 mesh manifest를 함께 읽어 equality assert. | **CLEAR — 279 전 필수** |
| C3-06 | T3 | `u_mean_ms * inlet_profile_G == u_target_ms` | `solver_code_260616.py:87-134`, `:933-951` | `u_mean_ms = u_target/G`가 legacy coefficient라는 정의는 유지하되, **그 정의가 실제로 지켜졌는지**는 아무도 검사하지 않는다. 기존 non-None `u_mean_ms`는 보존되므로 과거에 잘못 채워진 값도 살아남는다(Astra A1). | `apply_parsed_inlet_profile_g` 안. | **CLEAR** |
| C3-07 | T3 | `run_id` 문자열 ↔ `u_target_ms` / `p_gauge_pa` | `run_id="u0p2_p6M"`, `u_target_ms=0.2`, `p_gauge_pa=6.0e6` (`batch_config.py:399-450`, `manifest.py:423-482`) | 279개 `(geo_id, u, p)`를 손으로 편집하는 구조(Astra D-02)에서 이름과 파라미터가 어긋나면 **결과가 잘못된 run_id 아래 저장된다**. 파일 시스템 레이아웃 전체가 run_id 기반이라 사후 교정이 매우 비싸다. | run manifest 작성 시 또는 campaign matrix generator. | **CLEAR — 279 전 필수** |
| C3-08 | T3 | `periodic_shift_y_m ≈ domain_extent_y_m` | 좌변 mesh manifest, 우변 파싱되나 미저장(C1-14) | Diamond는 `periodic_shift_y`가 0.943–4.900 mm로 크게 변하는데 CAD와 config가 어긋나도 검출되지 않는다. 단 `domain_layout.py:25-29`가 기록한 대로 현재 geometry에서도 measured 0.003469131 m vs nominal 0.003465 m로 **약 0.12% 차이가 정상적으로 존재**하므로 엄격한 equality는 불가. | C1-14로 extent 저장 후 meshing gate에서 완화된 tolerance로 검사. | **NEEDS-ASTRA** — 0.12% 차이의 원인을 확정해야 tolerance를 정할 수 있다. |
| C3-09 | T3 | `pp_m_in ≈ rho * u_target_ms * A_inlet` | `pp_m_in`은 CSV, `u_target_ms`는 run manifest, `A_inlet`은 UDF가 계산해 출력하나(`udfs/260822_RO_UDF.c:1268-1270`) Python 쪽에는 없다 | inlet profile hook과 G 보정이 실제로 요청한 평균 속도를 만들어냈는지에 대한 **end-to-end 검사**. 현재는 G가 band 안이라는 것만 확인하고(C1-05/06) 결과 질량 유량은 확인하지 않는다. Astra B-06(empty-channel dP excess)의 원인 후보 중 "실제 유량이 의도와 다름"을 배제하거나 확정할 수 있다. | UDF가 inlet area를 transcript marker로 내보내고 extract에서 `m_in`과 대조. | **NEEDS-ASTRA** — 허용 오차가 물리적으로 무엇을 의미하는가(permeation에 의한 유량 변화 포함). |
| C3-10 | T3 | mesh manifest `mesh_sha256` ↔ 실제 `.msh.h5` | `manifest.py:293-420`은 hash **형식**만 검사 | Astra A-05와 동일. 다만 `rebuild_mesh_manifest.py:311-319`는 실제 SHA를 계산해 대조하는 코드를 **이미 갖고 있다** — 구현은 존재하고 live path에 없다. | solver preflight와 extract preflight. | **CLEAR** — 기존 로직 재사용. |

### 이미 존재하는 cross-field 검증 (재제안 금지)

- `assert_midplane_c_b_matches_boundary_mixing_cup` (`fluent_report_helpers.py:291-330`): mid-plane c_b vs 양옆 x-normal mixing-cup, rel_tol gate, 실패 시 raise. **C3-02/03의 구현 모델로 그대로 쓸 것.**
- `lmh_relative_difference` (`lmh_mass_balance` vs `lmh_udm_avg`, `LMH_REL_ABS_MAX=1e-3`).
- `mass_balance_relative_error` (boundary permeate vs total sink volume integral, `1e-3`).
- `EvaluationWindow._require_fits` (`domain_layout.py:246-253`): `n_lead + n_trail < n_active`.
- `fluent_report_helpers.py:490-602`: legacy 대칭 alias와 asymmetric 필드가 함께 오면 conflict raise.
- `manifest_validation.py:183-226`: `periodic_shift_y_m`의 `derived_from_angle` label 검사(수치 identity 아님, C3-08 참조).
- `apply_parsed_inlet_profile_g` (`solver_code_260616.py:933-951`): transcript G vs stored G, 1e-6 relative.

### T3 부수 관찰 (NEEDS-ASTRA)

`n_lead_excluded=3`은 측정 근거가 있는 결정이므로 뒤집지 않는다. 항등식 관점의 사실만 기록한다: Diamond에서 pitch가 0.943–4.900 mm로 변하므로 "lead 3 cells"의 물리적 길이는 **2.83 mm에서 14.70 mm까지 5.2배** 달라지고, `D2450_a60`(n_active=5)에서는 evaluation window에 cell **2개**만 남는다. `domain_layout.py:305-309`의 주석도 "whether decay follows cell count or an absolute length is still open"이라고 이미 적고 있다. → 배제 기준이 cell 수인지 절대 길이인지, window 2 cell로 dP spread WARNING이 의미를 갖는지 결정 필요.

---

## T4 — batch driver의 retry / skip / recovery

### 스테이지별 요약

| 스테이지 | transient로 보고 재시도 | permanent | skip 조건 | skip 근거가 "파일 존재"인가 |
|---|---|---|---|---|
| `batch_meshing.py` | CAD `AttachAssembly`, session socket-reset (`:146-155`). `transient_failure_max_retries=2` → 최대 3회. 실패 후 `post_failure_settle_s=15 s` 강제 대기 + leftover 프로세스 스캔(`:250-275`) | 그 외 worker nonzero (`:374-378`) | `skip_existing_mesh and mesh_exists` (`:119-126`, 호출 `:584-588`) | **예 — `os.path.isfile(expected_mesh)` 단독.** manifest 유무, `mesh_sha256`, mesh log, quality gate 통과 여부를 보지 않는다 → C4-01 |
| `batch_solver_sweep.py` | **없음. retry 전혀 없다** | 모든 worker nonzero | `skip_existing_final_data and final_pair_exists` (`:203-217`) | **예 — final `.cas.h5` + `.dat.h5` 존재 단독.** 크기 0, 이전 attempt, `RUNNING` manifest, FAIL quality를 구분하지 않는다 → C4-02 |
| `batch_solver_rerun.py` | **없음** (URF/pseudo-transient 실패는 재시도가 아니라 무시, C2-39) | 모든 실패 | 이전 results CSV의 `rerun_status ∈ {SUCCESS, SUCCESS_PROMOTED, SUCCESS_ATTEMPT_ONLY}` (`:1222-1243`) | 아니오 — CSV 상태 문자열 기반. 그 CSV가 현재 artifact와 일치하는지는 미확인 → C4-03 |
| `batch_report_extract.py` | **없음** | worker nonzero, 그리고 exit 0이어도 `validate_summary_wide_csv` 실패면 `FAILED_METRIC_VALIDATION` | `SKIP_EXISTING_REPORTS and summary_wide_csv.is_file() and validate_summary_wide_csv(...)` (`:429-446`) | **아니오 — 유효성까지 검사한다(모범 사례).** 다만 **freshness는 검사하지 않는다** → C4-04 |
| `batch_postprocess_all_cases.py` | report stage의 session socket-reset / Scheme heap corruption (`:950-964`, `:967-`). 2회 재시도, 매번 새 프로세스·새 session. **Canonical CP / load-bearing 실패는 session signature가 없으면 재시도하지 않는다** — 올바른 설계 | Canonical CP 실패, load-bearing 실패, missing `.cas` | 산출물 존재: `summary_wide.is_file()`(`:1133-1135`), `has_all_pyensight_outputs`(`:571`), `has_shear_outputs`(`:575`) | **예 — 산출물 파일 존재 단독** → C4-05 |
| `case_inventory.py` | 해당 없음 | 해당 없음 | 읽을 수 없는 manifest skip (`:560-566`) | 해당 없음. `run_inventory`는 **항상 `return 0`** (`:2230-2304`) → C4-06 |

### findings

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| C4-01 | T4 | `scripts/batch_meshing.py:119-126,584-588` | mesh skip이 `.msh.h5` 존재만 본다 | manifest write가 실패한 mesh(C4-07), quality gate에 걸렸지만 파일은 남은 mesh, 이전 config로 만든 mesh가 모두 "이미 있음"으로 영구 skip된다. Diamond 31개를 새로 굽는 상황에서 부분 실패가 조용히 남는다. | **CLEAR — 279 전 필수** — skip 조건에 `manifest.json` 존재 + `mesh_sha256` 일치 + 현재 config와 layout 필드 일치 추가. |
| C4-02 | T4 | `scripts/batch_solver_sweep.py:203-217` | solver skip이 final case+data 존재만 본다 | C2-17로 만들어진 run(manifest `RUNNING` + 파일 존재 + exit 0)이 **영구히 완료로 취급**된다. 크기 0 파일, 중단된 write, 이전 mesh 결과도 동일. `solver_common.py:304-310`에 크기 검사 helper가 이미 있는데 이 경로는 쓰지 않는다. | **CLEAR — 279 전 필수, 최우선** — "terminal stop_reason + 비어있지 않은 파일 쌍 + `mesh_sha256` 일치"로 강화. |
| C4-03 | T4 | `scripts/batch_solver_rerun.py:1222-1243` | 이전 results CSV 상태로 skip | 현재 artifact/manifest와의 일치를 확인하지 않는다. 다만 CSV 읽기 실패는 전부 재실행으로 degrade하므로 방향은 안전. | **CLEAR** — 우선순위 낮음. |
| C4-04 | T4 | `scripts/batch_report_extract.py:429-446` | 유효한 `summary_metrics_wide.csv`가 있으면 skip | **freshness 미검사.** solver를 다시 돌려 새 `.dat.h5`를 만들어도 이전 solve의 유효한 CSV가 있으면 skip → CSV가 **stale**해진다. `batch_solver_rerun`으로 결과를 promote한 뒤 이 드라이버를 돌리는 순서에서 실제로 발생한다. | **CLEAR — rerun을 쓸 계획이면 279 전 필수** — `summary_wide_csv` mtime이 `final_data_file`보다 오래되었거나 기록된 hash가 다르면 재실행. |
| C4-05 | T4 | `scripts/batch_postprocess_all_cases.py:571-586,747,1133-1154` | stage skip이 산출물 파일 존재(PNG glob 포함) 기준 | C4-04와 같은 stale 문제. contour/shear는 내용 검증조차 없다. Astra A1이 지적한 "서로 다른 extract의 stale file 혼입"이 여기서 발생한다. | **CLEAR** — 그림을 비교에 쓰기 전 필수. 279 solver sweep은 막지 않음. |
| C4-06 | T4 | `scripts/case_inventory.py:2230-2304` | `run_inventory`가 항상 `return 0` | inventory는 279-run coverage 확인 도구인데, manifest를 읽지 못해 case가 빠져도(C2-28) 성공으로 끝난다. **coverage 검증의 기반이 스스로 실패를 보고하지 못한다.** | **CLEAR — 279 전 필수** — skipped manifest가 있거나 기대 case 수와 다르면 nonzero. |

### dead end — 비싼 작업을 다시 해야만 복구되는 상태

| id | dead end | 왜 복구 불가인가 | 잃는 비용 | triage |
|---|---|---|---|---|
| C4-07 | mesh manifest write 실패 (요청문의 알려진 사례, 확정) | `meshing_code_260616.py:1137-1146`은 `.msh.h5`를 먼저 쓰고 manifest를 나중에 쓴다. 실패하면 mesh 파일만 남는다. `rebuild_mesh_manifest.py:301`은 `_read_mesh_manifest_for_rebuild(mesh_directory)`로 **기존 manifest를 먼저 요구**하고, discovery 루프(`:288-290`)도 `manifest.json`이 있는 leaf만 yield하며, `_build_cfg_overrides`도 layout을 기존 manifest에서 가져온다. **단 원자재는 남아 있다** — `mesh_log_{mesh_id}.txt`(`parse_meshing_input_summary`가 config 복원 가능), `mesh_run_record.json`(mesh_parameters + metrics + 경로), `.msh.h5`(SHA 계산 가능), `campaign_geometry` registry. | mesh 재생성 1건 (Diamond 대형 mesh는 특히 비쌈) | **CLEAR — 279 전 필수** — `rebuild_mesh_manifest`에 `--from-log` 모드 추가. 재meshing보다 훨씬 싸다. |
| C4-08 | solver crash로 manifest 없는 run leaf | Astra C-02 확정: run directory 생성 → launch/switch → **그 다음** RUNNING manifest 기록. launch 또는 `switch_to_solver()` 실패는 manifest 없는 leaf를 남긴다. `layout_from_run_directory`, inventory, extract 모두 run manifest를 요구하므로 어떤 도구로도 다룰 수 없다. | leaf를 손으로 지우고 run 재실행 (u=0.3 case는 수십 분) | **CLEAR** — launch **전에** attempt manifest(state=`LAUNCHING`) 기록. |
| C4-09 | stop reason 판정 실패로 `RUNNING`인 채 artifact만 존재 | C2-17 + C4-02의 결합. manifest는 `RUNNING`인데 sweep은 파일 존재만 보고 skip하므로 **자동으로는 절대 다시 실행되지 않는다.** 사람이 manifest를 읽어야 발견되고, inventory는 보고할 수 있지만 exit 0이다(C4-06). | 발견이 늦으면 279개 중 일부가 검증되지 않은 채 최종 비교에 들어간다 | **CLEAR — 279 전 필수.** C2-17과 C4-02를 함께 고치면 해소. |
| C4-10 | extract가 유효한 CSV를 남긴 뒤 solver를 다시 돌린 경우 | C4-04. CSV가 유효하므로 skip되고 stale CSV가 merged summary와 그림에 들어간다. 자동 복구 없음 — 사람이 `--force`를 줘야 한다. | 잘못된 비교 결과 (재계산은 extract 1회, 약 39분/case) | **CLEAR** — C4-04로 해소. |
| C4-11 | `batch_report_extract`가 실패를 안고 exit 0 (C2-24) | 상위 스크립트/CI가 실패를 감지하지 못한다. 실패 case의 존재가 status CSV를 사람이 열어야만 드러나고, status CSV 저장마저 실패하면(C2-26) 흔적이 없다. | 실패 case 발견 지연 | **CLEAR — 279 전 필수.** |
| C4-12 | leftover Fluent 프로세스 목록 조회 실패 (C2-37) | 남은 프로세스가 없다고 보고되면 settle 로직이 대응하지 못하고 다음 case가 socket 경합에 들어간다. 연쇄 실패는 retry 예산(2회)을 소진시키고 permanent 실패로 기록된다. | 연쇄 재meshing | **CLEAR** — 조회 실패 시 보수적 추가 대기. |

### T4 부수 관찰

- `batch_solver_sweep.py`에는 재시도가 **전혀 없다.** `batch_meshing`(CAD/socket)과 `batch_postprocess_all_cases`(socket/Scheme heap)에는 있다. 최근 commit `efbced9`가 extract에 transient 재시도를 넣은 것과 대비하면, **279-run에서 가장 비싼 스테이지(solver)만 transient crash 보호가 없다.** → **NEEDS-ASTRA**: solver crash를 자동 재시도해도 안전한가(부분 write된 `.dat.h5`, 라이선스, 수렴 이력).
- `batch_postprocess_all_cases`의 재시도 설계는 모범이다: session signature가 있을 때만 재시도하고 Canonical CP/load-bearing 실패는 wrapper 안에 있어도 재시도하지 않는다(`:950-964`). 다른 드라이버에 복제할 때 이 구분을 유지해야 한다.

---

## T5 — repository hygiene

### 5a. live path에서 호출되지 않는 함수 / 모듈

`docs/PIPELINE_MAP.md` §4.1은 dead symbol 7개를 이미 열거한다. 아래는 그 목록을 **검증하고 확장**한 것이다.

| id | task | 대상 | 상태 | triage |
|---|---|---|---|---|
| C5-01 | T5 | `docs/PIPELINE_MAP.md:190-191`이 dead로 기재한 `upgrade_mesh_manifest_in_place` / `upgrade_run_manifest_in_place` | **문서가 틀렸다.** 세 live script가 호출한다: `rebuild_mesh_manifest.py:342`, `backfill_mesh_manifest_fields.py:120`, `backfill_run_manifest_fields.py:146`. 같은 문서 §4.7이 그 backfill script를 복구 절차로 권장하고 있어 **한 파일 안에서 자기모순**이다. | **CLEAR** — §4.1에서 두 항목 삭제. |
| C5-02 | T5 | `src/ro/manifest_validation.py:290-330` `validate_spacer_wall_zones` (+ helper `collect_spacer_wall_zones_from_fluent:269`) | **test-only 확정.** 모듈 내부에서도 호출되지 않는다. `manifest.py:49-52`는 `validate_mesh_geometry_fields` / `validate_run_geometry_fields`만 import한다. 이것이 Astra A1이 요구한 "선언된 spacer wall 집합 vs 실제 zone의 exact coverage" gate 그 자체다 — **구현은 있고 배선만 없다.** | **CLEAR — 279 전 필수** — solver preflight에 연결. ML/Pillar/Sinusoidal은 spacer label이 split되어 있어 Diamond보다 위험하다. |
| C5-03 | T5 | `src/ro/domain_layout.py:470-499`, `:501-532` | test-only (C1-15). | **CLEAR — 279 전 필수.** |
| C5-04 | T5 | `src/ro/domain_layout.py:398-420` `mesh_case_name_from_solver_replace_log`, `:423-445` `assert_replace_log_matches_mesh_manifest` | **test-only.** solver replace log의 mesh 경로와 manifest `mesh_id`를 대조하는 검사인데 어떤 live path도 호출하지 않는다. Astra A-05의 "mesh 파일이 교체되어도 metadata는 정상처럼 보임"의 직접 방어책이다. | **CLEAR — 279 전 필수** — solver 및 extract preflight에 연결. |
| C5-05 | T5 | `src/ro/campaign_geo_ids.py:82-100` `assert_no_legacy_ml_geo_paths` | **test-only.** `M_r*` legacy ML 경로가 data root에 남아 있는지 확인하는 검사인데 실행되지 않는다. `C:/ro_data`의 실제 상태는 **UNKNOWN**. | **CLEAR** — 279 시작 전 1회 실행. |
| C5-06 | T5 | `manifest.iter_mesh_manifests`, `mesh_common.assert_mesh_case_name_matches` / `mesh_case_name_provenance` / `evaluate_quality_gate`, `manifest_validation.validate_metre_field_scales` 등 약 30개 | test-only이지만 각각 상위 wrapper(`apply_mesh_quality_gate`, `validate_*_geometry_fields`)를 통해 live로 이어지거나 진단 목적이다. **C5-02/03/04/05와 달리 배선 누락이 아니다.** | 조치 불필요. |
| C5-07 | T5 | 완전 dead(자기 파일 안에서도 미호출): `cp_metrics.cp_l2_bae_approx_expression`, `cp_metrics.build_window_cp_metric_keys`, `lmh_metrics.lmh_denominator_area_report_name`, `residual_transcript.transcript_max_iteration`, `udm_layout.require_ro_analytic_cwall` | `docs/PIPELINE_MAP.md` §4.1과 **정확히 일치**. 문서가 최신이다. | 조치 불필요(원하면 삭제). |
| C5-08 | T5 | `src/ro/fluent_report_helpers.py:968-994` `load_bearing_summary_missing_columns` / `is_legacy_summary_wide_schema` | live extract가 호출하지 않는다 (C2-09). 사후 도구와 tests만 사용. | **CLEAR — 279 전 필수** (C2-09와 동일 항목). |

### 5b. legacy naming 잔재

| id | task | file:line | 무엇인가 | triage |
|---|---|---|---|---|
| C5-09 | T5 | `scripts/_tmp_probe_reduction.py:42`, `scripts/_tmp_cell_profile.py:41,82` | 아카이브 case 이름 `D2450_a45_7c_brg110`이 기본값/조회 키로 하드코딩. 현재 체계는 `D2450_a45`이고 `_7c_brg110`은 옛 mesh 변형 접미사다. `_tmp_cell_profile.py:82`는 그 이름을 dict 키로 써서 상수를 조회하므로 현재 geo_id로 실행하면 조회에 실패한다. | **CLEAR** — `_tmp_*`는 campaign wiring이 아니다(`PIPELINE_MAP` §4.3). 삭제 또는 manifest 기반 전환. |
| C5-10 | T5 | `scripts/_tmp_probe_reduction.py:51-62,218-220` | D2450_a45 layout 상수 전체 하드코딩 (C1-18과 동일). | **CLEAR** — C1-18과 함께. |
| C5-11 | T5 | `scripts/batch_meshing.py`, `scripts/batch_solver_sweep.py` header comment | `My_CFD_Project/01_Scripts/...` 경로가 남아 있다. `PIPELINE_MAP` §4.6이 이미 기록. | **CLEAR** — 정리만. |
| C5-12 | T5 | `--results-root` flag | `case_inventory` / `residual_measurement_report` / `batch_postprocess`에서는 **runs root**, `batch_solver_rerun.resolve_path_defaults`에서는 **inventory root**. 이름은 같고 의미가 반대. `PIPELINE_MAP` §4.4가 이미 기록. | **CLEAR** — 279 전 rerun 쪽 flag 이름 변경 권장(잘못된 root로 279개를 훑을 위험). |
| C5-13 | T5 | 요청문의 "geo_id를 bare `"empty"`와 비교" 사례 | **현재 commit에서는 찾지 못했다.** 남은 `== "empty"` 비교는 모두 `family` 필드 대상이고(`manifest.py:280`, `manifest_validation.py:72`, `meshing_code_260616.py:333`, `run_config.py:607`), family 디렉터리 이름이 실제로 `empty`이므로 정상이다. `family_for_geo_id("REF_empty") -> "empty"`(`campaign_geo_ids.py:70-71`)가 이 매핑의 single source다. | 조치 불필요 — 이미 수정된 것으로 판단. |
| C5-14 | T5 | 요청문의 "교체된 geometry id 체계 참조" | `LEGACY_ML_GEO_ID_PREFIX = "M_r"`(`campaign_geo_ids.py:26`)만 남아 있고 legacy 탐지용으로 **의도적으로 보존된 상수**다. 다만 그 탐지기가 실행되지 않는다(C5-05). | C5-05와 동일. |

### 5c. docs가 코드와 모순되는 진술

| id | task | 문서 진술 | 코드 사실 | triage |
|---|---|---|---|---|
| C5-15 | T5 | `docs/PIPELINE_MAP.md:190-191`: `upgrade_*_manifest_in_place`는 "Defined but never called", "`scripts/migrate_manifest_schema_v2.py` and its tests are gone; live path uses `write_mesh_manifest`" | `rebuild_mesh_manifest.py:24,342`, `backfill_mesh_manifest_fields.py:23,120`, `backfill_run_manifest_fields.py:24,146`에서 호출된다. 같은 문서 §4.7이 이 backfill script들을 복구 수단으로 지시한다. | **CLEAR** |
| C5-16 | T5 | `docs/PIPELINE_MAP.md:249` (§4.6): "`configs/batch_config.py` holds the **31-mesh campaign matrix** (9 diamond + 3 ML + 9 pillar + 9 sin + `REF_empty`)" | 모듈을 실제로 로드해 확인: `len(mesh_batch_cases) == 22`, family 분포 `{pillar: 9, sin: 9, ml: 3, empty: 1}`. **diamond 0개.** `_DIAMOND_MESH_LAYOUTS`(`:488-498`)는 "Reference data, not used by this config" 주석과 함께 존재할 뿐 append되지 않는다. Astra D-02가 runbook에 대해 지적한 것과 같은 문제가 PIPELINE_MAP에도 있다. | **CLEAR — 279 전 필수** (문서만 고치면 안 되고 C1-03의 matrix generator가 함께 필요). |
| C5-17 | T5 | `docs/PIPELINE_MAP.md:249` (§4.6): "a two-case `solver_sweep_cases` starter (`REF_empty` / `u0p2_p6M` and `u0p3_p6M`)" | 실제 `len(solver_sweep_cases) == 5` — REF_empty 2건 + `D2450_a45` p8M 2건(mesh_id `..._bl4_peel2` / `..._bl6_peel2`) + `D0817_a45` u0p3 1건. | **CLEAR** |
| C5-18 | T5 | `docs/PIPELINE_MAP.md:232`, `docs/GEOMETRY_DESIGN.md:285`: `validate_spacer_wall_zones`가 test-only라고 기술 | 코드와 **일치**. 문서는 맞고 문제는 배선 부재 자체다(C5-02). | 문서 조치 불필요. |
| C5-19 | T5 | `docs/PIPELINE_MAP.md:186` §4.1 / §4.2 | 목록 자체는 C5-01 두 항목을 빼면 정확하다. 다만 **test-only validator는 §4.2에 `resolve_layout` 하나만** 있고, 실제로는 배선이 필요한 test-only validator가 최소 4개 더 있다(C5-02/03/04/05). | **CLEAR** — §4.2를 "test-only, 배선 필요" 목록으로 확장. |
| C5-20 | T5 | `docs/AGENTS.md:151-152`: "Run the full pytest suite; it must stay green ... (WSL **784 passed**)" | 실제 `1056 passed, 3 failed, 1 skipped`. C6-04 참조. | **CLEAR — 279 전 필수** |
| C5-21 | T5 | Astra D-04의 잠정 항목(centered-z 주석 혼재, `periodic_after_surface_mesh=False` 역사 기록, CP max 방향 서술 오류) | 이번 pass에서 재검증하지 않았다. Astra 기록 유지. | Astra D-04 참조. |

### 5d. 중복 구현

`docs/PIPELINE_MAP.md` §4.4가 5개 pair를 이미 기록한다. 아래는 **§4.4에 없는 것만**.

| id | task | 중복 | 관계 / 위험 | triage |
|---|---|---|---|---|
| C5-22 | T5 | `list_named_object_names`가 **4곳**: `scripts/solver_code_260616.py:610-637`, `scripts/pyfluent_report_extract.py:420-453`, `scripts/pyfluent_shear_contour_export.py`, `src/ro/fluent_report_helpers.py:25-55` | 네 사본 모두 같은 3단 fallback(`list_1` / `get_state` / 예외 → `[]`). `src/ro`에 정본이 있는데 script들이 각자 복사했다. C2-05의 "error와 empty 구분" 수정을 하려면 4곳을 고쳐야 하고 한 곳을 빠뜨리면 조용히 남는다. | **CLEAR** — `fluent_report_helpers` 버전으로 통합. |
| C5-23 | T5 | `create_x_normal_plane` / `create_z_normal_plane`이 `scripts/pyfluent_report_extract.py:694-735`와 `src/ro/fluent_report_helpers.py:58-152`에 각각 | extract는 x-normal은 helper로 위임(`:696`)하면서 z-normal은 **자체 구현**을 갖고 있다(`:699-735`). 두 z-normal 구현이 미묘하게 다르면 mid-plane 위치가 경로에 따라 달라진다 — **canonical CP의 denominator가 여기에 걸려 있다.** | **CLEAR — 279 전 필수.** |
| C5-24 | T5 | URF 스택 5개 함수가 `scripts/solver_code_260616.py`와 `scripts/batch_solver_rerun.py`에 각각 복제: `set_and_verify_leaf`, `set_and_verify_dict_entry`, `apply_pseudo_time_species_relaxation`, `apply_species_implicit_under_relaxation`, `apply_real_under_relaxation` | solve와 rerun이 **다른 numerics로 갈라질 수 있고** 그 사실이 어디에도 기록되지 않는다(C2-18/C2-39). | **CLEAR** — `src/ro`로 추출. C2-18 수정과 함께. |
| C5-25 | T5 | `derive_spacer_cell_metrics` vs `..._for_layout`, `derive_periodic_spacer_pressure_metrics` vs `..._for_layout` (`fluent_report_helpers.py:2413-2579`) | 대칭 legacy와 asymmetric 버전. legacy는 `n_buffer_cells_each_end`를 요구하는데 asymmetric layout에서 그 값은 `None`이다. legacy 둘 다 현재 test-only. | **CLEAR** — legacy 2개 제거 (C1-09과 동일). |
| C5-26 | T5 | `write_csv_file`이 `scripts/case_inventory.py:2052-2057`과 `scripts/batch_postprocess_all_cases.py:1423-1429`에 각각 (본문 거의 동일, `extrasaction="ignore"` 포함) | C1-11의 "per-cell 열 무음 폐기"를 고치려면 두 곳을 고쳐야 한다. | **CLEAR** — 우선순위 낮음. |
| C5-27 | T5 | `_has_windows_drive`가 `pyensight_contour_export.py:67`, `pyensight_extra_figures.py:514`, `pyfluent_shear_contour_export.py:173` 3곳 | WSL/Windows 경로 판별의 3중 복제. 동작 차이는 발견하지 못했다. | **CLEAR** — 우선순위 낮음. |

---

## T6 — 전체 test suite

### 실행 결과

```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
3 failed, 1056 passed, 1 skipped in 2.25s
```

**Astra D-01의 `1056 passed, 3 failed, 1 skipped`와 동일하다.** commit `d453e0a` 기준으로 여전히 유효하며 실패 항목도 같다. test/source는 변경하지 않았다.

| id | task | 실패 test | 원인 | 기대값이 틀렸나, 코드가 틀렸나 | triage |
|---|---|---|---|---|---|
| C6-01 | T6 | `tests/test_backfill_run_manifest_fields.py:45` `test_backfill_adds_missing_geometry_field_only` | `additions == {"membrane_blocked_area_frac_geometric": 0.0}`를 기대하지만 실제는 `None`. `GEO_ID = "D2450_a45"`이고 `geometry_parameters_for_geo_id("D2450_a45")["membrane_blocked_area_frac_geometric"]`는 **`None`**이다 (직접 실행 확인; Pillar `P_p60_h00`만 `0.047`). backfill은 registry에서 그대로 가져온다(`backfill_run_manifest_fields.py:137`). | **test 기대값이 틀렸다.** schema는 이 필드를 `float in [0,1) \| null`로 정의하고(`manifest.py:12`), Diamond는 geometric footprint가 계산되지 않아 `None`이 올바르다. 혼동의 원인은 형제 필드 `membrane_blocked_area_frac`이 `0.0`이라는 것 — 두 필드는 다른 양이다. | **CLEAR** — 기대값을 `None`으로 수정. 부수 관찰 참조. |
| C6-02 | T6 | `tests/test_backfill_run_manifest_fields.py:78` `test_backfill_apply_writes_valid_manifest` | C6-01과 같은 원인 (`read_back[...] == 0.0` vs `None`). | **test 기대값이 틀렸다.** | **CLEAR** — 동일 수정. |
| C6-03 | T6 | `tests/test_inventory_convergence_classification.py:216` `test_inventory_csv_schema_unchanged` | `len(CASE_INVENTORY_FIELDNAMES) == 120`, 실제 `123`. AST diff로 확인한 추가 3개: **`convergence_quality_warnings`, `pp_pressure_drop_rel_spread_window`, `pp_pressure_drop_rel_spread_note`**. 삭제된 필드는 없다. 모두 commit `c315b49`(dP spread WARNING 도입) / `f72190f`(evaluation window로 채점)에서 **의도적으로** 추가됐다. | **test 기대값이 틀렸다** — 그리고 assertion 자체가 나쁜 계약이다. 개수만 세므로 필드 이름/의미/단위 변경은 못 잡고 추가만 잡는다. Astra D-01의 "의미 없는 count assertion" 판단이 맞다. | **CLEAR** — count를 **필드 이름 집합 exact match**로 교체. 279 전 필수는 아니나, 이 test가 계속 깨지면 suite 전체가 무시된다. |
| C6-04 | T6 | `docs/AGENTS.md:151-152` | "Run the full pytest suite; it must stay green on both platforms (WSL **784 passed**; Windows without symlink privilege **783 passed, 1 skipped**)." | **docs-code 모순.** 실제는 `1056 passed, 3 failed, 1 skipped`. 기록된 baseline은 272개 test 이전 것이고, 무엇보다 **green이라고 단언**하지만 현재는 green이 아니다. | **CLEAR — 279 전 필수** — baseline 갱신 + 세 실패를 고치거나 명시적 xfail. green이 아닌 상태에서 "green이어야 한다"는 지침은 앞으로의 모든 회귀를 가린다. |

### full suite를 gate로 실행하는 것이 있는가 — **없다**

- `.github/` 디렉터리가 **존재하지 않는다** (CI workflow 없음).
- `.git/hooks/`에 sample이 아닌 활성 hook이 **없다** (pre-commit / pre-push 없음).
- `.claude/settings.local.json`은 permission allowlist뿐이고 hook 정의가 없다.
- `pyproject.toml`은 `[tool.pytest.ini_options]`로 `pythonpath = ["src"]`만 설정한다. test를 강제하는 target/script 없음.
- `docs/DEPLOY_RUNBOOK.md:54`와 `docs/AGENTS.md:151`이 "full suite를 돌려라"라고 **지시**하지만 강제하지 않는다.

→ 요청문의 추정("partial invocations appear to have masked these failures")이 맞다. 세 실패는 전부 **최근 commit이 의도적으로 필드를 추가/변경하면서 그 test를 갱신하지 않은 것**이며, 실행 강제 장치가 없어 누적됐다.

### T6 부수 관찰 (NEEDS-ASTRA)

C6-01/02를 "기대값을 `None`으로 바꾸면 끝"이라고 처리하기 전에 확인할 것: backfill은 **필수 필드를 `None`으로 채워 schema validation을 통과시킨다**. 즉 `read_run_manifest`가 다시 읽히지만 값은 "모름"이다. Astra A-03(required field 추가가 34/44 leaf를 깨뜨린 사건)의 복구 경로가 바로 이것이므로, "필수 필드가 `None`으로 존재"와 "필수 필드가 없음"을 schema가 구분해야 하는지는 설계 결정이다. → nullable required field의 의미를 확정할 것.

---

## 279-run sweep 전에 반드시 고쳐야 하는 것

### 1군 — Diamond가 처음 만드는 실패 (지금 고치지 않으면 소급 재작업)

1. **mesh manifest의 layout을 측정과 대조** (C1-13, C1-14, C1-15, C3-01). `build_mesh_manifest_payload`에 `domain_extent_x_m/y_m/z_m`를 추가하고, manifest write 직전에 `validate_layout_against_x_extent`를 호출해 불일치 시 abort. **Diamond mesh를 굽기 전에** 넣어야 한다 — 나중에 추가하면 31개 mesh를 다시 구워야 측정값이 생긴다.
2. **campaign matrix를 선언적으로 생성** (C1-03, C5-16, C5-17, C3-07). `_COMMON_MESH`를 복사해 Diamond를 추가하면 `n_active_cells=7`이 그대로 상속된다. `_DIAMOND_MESH_LAYOUTS` / registry에서 pitch·`n_active_cells`·`periodic_shift_y`·`m_max`를 주입하고 31 × 9의 정확한 cardinality와 `run_id` ↔ `(u, p)` 일치를 assert.
3. **`rebuild_mesh_manifest`에 `--from-log` 모드** (C4-07). 현재는 manifest가 없으면 재meshing이 유일한 복구다. 원자재는 전부 남아 있다.

### 2군 — 완료되지 않은 run이 완료로 보이는 경로

4. **stop reason 판정 실패를 실패로 처리** (C2-17).
5. **solver skip을 manifest 기반으로 강화** (C4-02, C4-09). 4번과 5번은 **함께** 고쳐야 의미가 있다.
6. **`batch_report_extract`가 실패 시 nonzero 종료** (C2-24, C4-11).
7. **`case_inventory`가 실패 시 nonzero 종료** (C2-28, C4-06).

### 3군 — 결과값을 조용히 바꿀 수 있는 것

8. **under-relaxation 적용 실패를 기록하고 abort** (C2-18, C2-39, C5-24).
9. **solver 설정 파싱 실패 시 default 대체 금지** (C2-13).
10. **`pp_area_mem == pp_udm_area_sum` 검증** (C3-02). 두 값이 이미 나란히 CSV에 있다.
11. **`pp_pressure_drop_spacer == Σ pp_pressure_drop_cell_N` 검증** (C3-03). 1번이 놓친 layout 오류의 독립 2차 방어선.
12. **`membrane_blocked_area_frac`의 campaign 정책(`== 0.0`)과 mesh↔run equality** (C3-04, C3-05).
13. **wide CSV blank load-bearing 열 gate를 live extract에 연결** (C2-09, C5-08). 구현은 이미 있다.
14. **z-normal plane 구현 이중화 제거** (C5-23). canonical CP denominator가 걸린 경로다.
15. **convergence quality의 manifest write 실패를 실패로 처리** (C2-04).

### 4군 — 이미 구현돼 있고 배선만 없는 gate (비용이 가장 싸다)

16. `validate_spacer_wall_zones` (C5-02) — solver preflight.
17. `assert_replace_log_matches_mesh_manifest` (C5-04) — solver/extract preflight.
18. `assert_no_legacy_ml_geo_paths` (C5-05) — campaign 시작 전 1회.
19. mesh SHA 실측 대조 (C3-10) — `rebuild_mesh_manifest.py:311-319`의 로직 재사용.

### 5군 — 문서와 test baseline

20. **`docs/AGENTS.md:151-152`의 "784 passed, green" 기재를 현실과 맞춤** (C6-04, C5-20).
21. **세 test 실패 정리** (C6-01/02/03). 전부 기대값이 stale이며 코드는 옳다.
22. **`PIPELINE_MAP.md` §4.1/§4.6 수정** (C5-01, C5-15, C5-16, C5-17).

## 나중으로 미뤄도 되는 것

- **그림/contour 경로 전반** (C1-16, C4-05, C4-10, T2 미착수 범위). 279 solver sweep을 막지 않는다. 단 **그림을 논문 비교에 쓰기 전에는 별도 pass가 필요하다.**
- **`_tmp_*` / `_probe_*` 스크립트의 하드코딩과 legacy 이름** (C1-18, C5-09, C5-10). campaign wiring이 아니다.
- **`batch_solver_rerun` 관련 항목** (C2-39, C4-03, C5-12). 단 rerun을 쓸 계획이면 C4-04(extract freshness)는 필수로 올라간다.
- **중복 helper 정리** (C5-22, C5-26, C5-27). 다만 C2-05를 고칠 때 `list_named_object_names` 4곳을 모두 고쳐야 한다.
- **legacy `derive_spacer_cell_metrics` 계열 제거** (C1-09, C5-25). 현재 test-only라 해를 끼치지 않는다.
- **per-cell 열의 CSV 무음 폐기** (C1-11). 원본 `summary_metrics_wide.csv`에는 남는다.

## Astra에게 넘기는 판단 항목 (NEEDS-ASTRA 요약)

| id | 무엇을 결정해야 하는가 |
|---|---|
| C1-05 | UDF `INLET_G_MAX = 1.02` band가 Diamond 전 범위의 inlet discretisation을 덮는가, geometry별 band가 필요한가. 벗어나면 run이 fail-closed로 정지한다(C1-06). |
| C1-10 | pitch가 0.943–4.900 mm로 변할 때 `pp_pressure_drop_cell2_over_cell3`가 family 간 비교 가능한 양인가. |
| C2-12 | `manifest._iter_child_dirs`의 `OSError → []`가 어떤 caller에서 "run 0건 = 정상"으로 이어지는가. |
| C2-16 | transcript 절단 시 마지막 유효 row가 "final residual"로 보고되는데, iteration 번호가 동반돼 절단을 검출할 수 있는가. |
| C2-19 | solve-time spacer pressure monitor 부재가 QoI stop 판정을 바꾸는가. |
| C2-23 | case-local UDF가 없을 때 master UDF로 `analytic_cwall`을 대체하는 fallback을 어디까지 허용할 것인가. |
| C3-02 | `pp_area_mem` vs `pp_udm_area_sum`의 허용 rel_tol. |
| C3-08 | `periodic_shift_y_m` vs measured y extent의 0.12% 차이 원인 — tolerance를 정하려면 필요하다. |
| C3-09 | `m_in ≈ rho·u_target·A_inlet`의 허용 오차가 물리적으로 무엇을 뜻하는가. |
| T3 부수 | `n_lead_excluded=3`은 유지하되, Diamond에서 lead 3 cell의 물리적 길이가 2.83–14.70 mm로 5.2배 변하고 `D2450_a60`은 window에 cell 2개만 남는다. 배제 기준이 cell 수인지 절대 길이인지, window 2 cell에서 dP spread WARNING이 의미를 갖는지. |
| T4 부수 | solver crash를 자동 재시도해도 안전한가(부분 write된 `.dat.h5`, 라이선스, 수렴 이력). 279-run에서 가장 비싼 스테이지만 transient 보호가 없다. |
| T6 부수 | nullable required field의 의미 — "`None`으로 존재"와 "없음"을 schema가 구분해야 하는가. |
