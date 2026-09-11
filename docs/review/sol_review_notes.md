# Sol repository review — mechanical first pass

## Progress — 이 파일이 검토 상태의 source of truth

- 검토 기준 commit: `d453e0a29fa954e51655954dca3604cc8279885d`.
- T1 hardcoded layout assumptions: **COMPLETE** — tracked live Python/C layout literals와 required grep patterns 전수 확인; tests의 fixed fixtures는 live path와 분리함.
- T2 success-permitting `except` clauses: **COMPLETE** — scope `scripts/` + `src/ro/`의 43 files, 730/730 clauses 전수 분류.
- T3 unchecked arithmetic identities: **PARTIAL** — batch 3 완료 (누적 12 files); solver/artifact identity가 다음 batch.
- T4 retry, skip and recovery: **NOT STARTED**.
- T5 repository hygiene: **NOT STARTED**.
- T6 full test suite: **NOT STARTED**.
- 범위 제한: `C:/ro_data`, 공용 Windows workstation, live Fluent 및 solver run에는 접근하지 않는다. 필요한 사실은 `UNKNOWN`으로 표시한다.
- 기존 `docs/review/astra_review_notes.md`는 한 번 읽었으며 수정하지 않는다. 완료된 A1 duplicated source-of-truth inventory는 재검토하지 않는다.
- source/config/tests는 변경하지 않는다. 이 파일만 작성한다.

## Triage tag

- **CLEAR**: 코드만으로 동작과 최소 수정이 명확함.
- **NEEDS-ASTRA**: physics, numerics, Fluent semantics 또는 깊은 cross-file 판단이 필요함.
- **UNKNOWN**: `C:/ro_data`, live Fluent 또는 solver run 없이는 판정 불가함.

## Findings

### T1 — hardcoded layout assumptions

#### Batch 1 — core layout/config (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-01 | T1 | `src/ro/convergence_quality.py:33,88-92,131-138` | `PRESSURE_DROP_CELLS=(4,5,6,7)`는 과거 continuity 열이고 scoring은 전달받은 `evaluation_cell_numbers`를 사용한다. | 30-cell에서도 crash는 없지만 continuity 열은 4개 cell만 보므로 window 통계로 오인하면 틀린 수치다. 이미 알려진 교정 사례이며 새 scoring 경로는 고쳐져 있다. | **CLEAR** — legacy 열 이름/설명을 유지하고 campaign 판정에는 `pp_pressure_drop_rel_spread_window`만 사용. |
| T1-02 | T1 | `src/ro/domain_layout.py:53-55,290-313` | `CURRENT_LAYOUT`은 `n_active=7`, `LEGACY_LAYOUT`은 `n_active=3`; D2450 길이 상수도 고정이다. | live import는 없고 tests만 참조하므로 30-cell run에는 wrong number/crash 없음. 이름이 generic하여 future reuse 위험만 있다. | **CLEAR** — `CURRENT_LAYOUT`을 geometry-specific fixture 이름으로 바꾸거나 test fixture로 이동; campaign 전 필수 아님. |
| T1-03 | T1 | `configs/batch_config.py:132-155,297-373,488-498` | `_COMMON_MESH`가 `n_active_cells=7`, `cell_length_x_m=0.003465`; Diamond별 `_DIAMOND_MESH_LAYOUTS`는 “not used by this config”이고 `mesh_batch_cases`에 연결되지 않는다. | 현재 22개 non-Diamond/reference case에는 맞지만, 279-run list를 같은 copy pattern으로 만들면 Diamond도 7-cell domain으로 생성된다. crash보다 잘못된 domain이 더 위험하다. | **CLEAR** — production matrix 생성 시 registry/layout table에서 Diamond의 `n_active_cells`, pitch, periodic shift, `m_max`를 주입하고 expected 31 geometry set을 assert. **279 전 필수**. |
| T1-04 | T1 | `configs/run_config.py:327-329,793-802` | QoI config에 legacy `domain_length_m=0.017325`, `buffer_length_m=0.003465`가 남고 해당 두 값끼리만 검증된다. | 현재 solver live report path는 mesh-manifest layout을 사용하므로 30-cell 수치에 직접 쓰이지 않는다. 그러나 validator가 실제 layout과 무관한 5-cell-era 값을 정상으로 인증해 misleading state를 만든다. | **CLEAR** — unused legacy keys/validation을 제거하거나 resolved mesh layout에서 생성해 equality를 검사; campaign 전 정리 권장. |

Batch 1 판독 결론: core live layout 객체 자체는 `n_active`/`n_total`을 동적으로 계산한다. 이 배치에서 30-cell index crash를 직접 만드는 core loop는 발견하지 않았다.

#### Batch 2 — live extract/helper/solver/inventory (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-05 | T1 | `src/ro/fluent_report_helpers.py:2568-2579` | `pp_pressure_drop_cell2_over_cell3`는 의도적으로 global cells 2/3을 고정한다. | campaign의 `n_buffer_in=1`에서는 첫 두 active cells이므로 30-cell에서도 crash/오계산 없음. 향후 buffer count 변경 시 이름과 의미가 어긋난다. | **CLEAR** — 현재는 유지; 일반화 시 `layout.active_cell_numbers()[:2]`로 만들고 실제 cells를 metadata에 기록. |
| T1-06 | T1 | `src/ro/fluent_report_helpers.py:874-907,2454-2487,2545-2566` | report name, per-cell metric, evaluation-window dP는 각각 `n_unit_cells` 또는 `layout.active_cell_numbers()`에서 동적으로 생성된다. 옛 symmetric helpers `:352-431,2414-2451,2490-2542`는 tests에서만 호출된다. | 30-cell wrong index는 없음. legacy helper가 live로 재사용되면 asymmetric buffers를 표현하지 못한다. | **CLEAR** — live code는 조치 없음; test-only legacy helpers에 legacy 표기를 붙이거나 제거. |
| T1-07 | T1 | `scripts/pyfluent_report_extract.py:939-969,1128-1183,1373,1519,1617` | `layout.n_total`, dynamic boundary list, `evaluation_window`로 8–30 cell을 계산한다. 고정 `Cell 7/8/10` 표기는 notebook-style phase label이지 domain cell 번호가 아니다. | Python indexing/count 관점의 30-cell crash는 찾지 못했다. 다만 30-cell은 surface/report object를 더 많이 만들므로 실제 `is_active`/Scheme failure 여부는 코드만으로 결정 불가하다. | **UNKNOWN** — live Fluent workstation에서 D0817 layout의 전체 report create/compute를 fault-injection 포함해 확인. |
| T1-08 | T1 | `scripts/solver_code_260616.py:1657-1680,3417-3430` | solve-time pressure planes는 mesh manifest의 `layout.active_span()`에서 유도된다. | 30-cell에 고정 7/10 적용 없음; 두 spacer-edge plane만 만들므로 layout count crash 없음. | **CLEAR** — 조치 없음. |
| T1-09 | T1 | `scripts/case_inventory.py:1314-1355` | window cells는 run-directory manifest에서 계산하며, 실패 시 window 값이 비어 있다. `pp_pressure_drop_rel_spread_cells_4_7`은 별도 continuity 열이다. | 30-cell에서 manifest가 정상이라면 dynamic. manifest read 실패 시 wrong fixed window가 아니라 blank/UNKNOWN으로 간다(T2에서 gate 분류). | **CLEAR** — layout hardcode 조치 없음; continuity 열을 scoring에 사용하지 않기. |

#### Batch 3 — diagnostic/figure defaults (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-10 | T1 | `scripts/_tmp_probe_reduction.py:37-64,217-221` | D2450_a45 전용 throwaway probe가 7 active/10 total, 11 planes 및 고정 x 좌표를 갖는다. | D0817_a30에 재사용하면 domain 뒤쪽 20 cells를 보지 않고 일부 region label도 틀린다; list index crash는 없음. 현재 production path가 아님. | **CLEAR** — 파일명을 D2450-specific으로 명시하거나 `layout_from_run_directory()`에서 계산; campaign 전 필수 아님. |
| T1-11 | T1 | `scripts/verify_load_bearing_report_names.py:2-5,21-34` | `--n-unit-cells` 기본값이 `10`이다. | 30-cell 이름 전체를 검증하지 않지만 현재 `LOAD_BEARING_REPORT_NAMES`는 per-cell 이름을 포함하지 않아 false PASS가 가능하다; crash 없음. | **CLEAR** — `--n-unit-cells`를 필수 인자로 만들거나 manifest path를 받아 `layout.n_total` 사용. |
| T1-12 | T1 | `scripts/batch_postprocess_all_cases.py:217,810-844` | contour default `--manual-view-bounds="0,0.010395,-0.0017325,0.0017325"`가 모든 case에 전달된다. | 긴 Diamond domain에서는 뒤쪽 evaluation cells를 화면에서 잘라내는 wrong figure를 만들 수 있다; scalar CSV와 solver에는 영향 없음. | **CLEAR** — run manifest의 evaluation/active span과 periodic width에서 case별 view bounds 생성. figures를 campaign 비교에 쓰기 전 필수. |
| T1-13 | T1 | `scripts/diagnose_cp_face_filters.py:52-59,633-634,910` | `CONTACT_WIDTH_OVER_PITCH=0.000152/0.003465`가 D2450 geometry 값인데 임의 run diagnostic metadata/threshold 설명에 사용된다. | 다른 pitch/family에 실행하면 contact-fraction reference가 틀리며 진단 해석을 오도한다; solver/crash 영향 없음. | **CLEAR** — run/mesh manifest의 `membrane_contact_width_m`와 pitch에서 계산하고 unknown이면 생략. |

#### Batch 4 — remaining CP/profile/contour diagnostics (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-14 | T1 | `scripts/_tmp_cell_profile.py:34-83,61-76,500-534` | case ID와 두 `area_mem` fallback은 geometry-specific이지만 cell counts/boundaries/window는 mesh manifest에서 읽는다. | 30-cell count/index 오계산 없음. 다른 case로 ID만 바꾸고 filename override를 안 바꾸는 수동 사용 위험은 T5 legacy/config 문제다. | **CLEAR** — layout 조치 없음. |
| T1-15 | T1 | `scripts/diagnose_cp_quantile_spread.py:569-588,868-872`; `scripts/diagnose_yi_saturation.py:852-879` | 두 diagnostic 모두 `layout_from_run_directory()`와 `evaluation_window.evaluation_cell_numbers()`를 사용한다. | 30-cell 고정 index 없음. 실제 Fluent에서 object 수 증가로 실패할지는 코드만으로 판단 불가하다. | **UNKNOWN** — live Fluent에서 30-cell diagnostic 완주 여부만 확인; Python layout 수정은 불필요. |
| T1-16 | T1 | `scripts/pyensight_contour_export.py:4182-4208` | 내부 고정 layout은 없지만 caller가 준 `manual_view_bounds`를 그대로 우선한다. | T1-12의 D2450 고정 bounds가 전달되면 30-cell figure가 잘린다. 원인은 caller default이며 이 파일 자체의 count bug는 아니다. | **CLEAR** — caller에서 case별 bounds를 만들고 이 파일은 supplied bounds provenance를 계속 기록. |

#### Batch 5 — meshing/registry/shear/extra figures (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-17 | T1 | `src/ro/campaign_geometry.py:66-77,277-300,355-450` | Diamond는 9개 `geo_id`별 5–27 active cells를 explicit table로 갖고, 나머지 families/reference의 7은 설계별 상수다. | registry 자체에는 30-cell truncation 없음. config가 값을 덮는 문제는 완료된 Astra A1이므로 여기서 재검토하지 않는다. | **CLEAR** — layout hardcode 조치 없음; T1-03의 production generator 연결만 필요. |
| T1-18 | T1 | `scripts/meshing_code_260616.py:45-64` | meshing manifest payload는 `cfg.n_active_cells`와 `cfg.cell_length_x_m`를 사용하며 내부 7/10 상수가 없다. | 30-cell 지원 여부는 config 정확성에 달렸고 자체 index crash는 없음. | **CLEAR** — T1-03 config fix 외 조치 없음. |
| T1-19 | T1 | `scripts/pyensight_extra_figures.py:591-600,2143-2162,2223-2229,2310-2313`; `scripts/pyfluent_shear_contour_export.py` | extra figures는 active wall bounds 또는 explicit bounds에서 slice 위치를 계산하고, shear exporter에는 streamwise cell count가 없다. | 30-cell hardcode에 의한 wrong number/crash 없음. actual wall bounds discovery 성공 여부는 live EnSight/Fluent가 필요하다. | **UNKNOWN** — workstation에서 automatic bounds가 전체 active membrane을 잡는지만 확인. |

#### Batch 6 — residual candidate files/global closure (4 files, 저장 완료)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T1-20 | T1 | `configs/post_config.py:106-132` | live extract layout/window defaults는 모두 `None`; batch override가 없으면 worker가 거부한다. | 7/10 fallback으로 30-cell을 잘못 처리하지 않는다. | **CLEAR** — 조치 없음. |
| T1-21 | T1 | `src/ro/cp_metrics.py:349-439` | cell aggregate는 caller가 준 `cell_numbers` sequence를 순회하며 고정 count가 없다. | 30-cell wrong index 없음. metric validity는 이 pass 범위 밖이며 Astra로 넘긴다. | **NEEDS-ASTRA** — aggregation semantics/finite-value validity만 별도 scientific review에서 판단. |
| T1-22 | T1 | `src/ro/residual_transcript.py:89-127` | `11/12` tokens, 5 residuals, 4 monitors는 transcript column schema이지 streamwise layout count가 아니다. | Diamond cell count와 무관하다. header 변화 시 parse 문제는 T2/T3 대상이지 30-cell layout bug가 아니다. | **CLEAR** — T1 조치 없음. |
| T1-23 | T1 | `scripts/make_summary_figures.py:569-581,661,771` | `n_total`은 summary CSV row count다. streamwise `layout.n_total`이 아니다. | 30-cell 영향 없음. | **CLEAR** — 조치 없음. |

T1 closure: required literals `7,10,11,24,25`, `range(2,9)`, `range(11)`, `[4:8]`, `cells_4_7`, `unit_cell_boundary_10`, `n_unit_cells`/`n_total` 상수를 tracked live code에서 검색했다. 새 production-critical layout defect는 T1-03 하나이며, T1-12는 figure-only defect다. 이미 알려진 4–7 scoring bug는 legacy continuity 열만 남고 live scoring은 dynamic이다.

| T1-24 | T1 | `scripts/_tmp_cell_profile.py:933-970` | convergence reference가 `spacer_3`, `spacer_4`, `spacer_5`의 평균으로 고정된다. | 30-cell에 실행하면 전체/evaluation window가 아니라 세 cells만 reference로 쓰며, diagnostic CSV label이 일반적인 convergence처럼 보인다; crash 없음. | **CLEAR** — `EVALUATION_WINDOW.evaluation_cell_numbers(LAYOUT)`에서 reference set을 만들거나 D2450-only 이름을 명시. production path가 아니므로 유예 가능. |

T1-24는 broader numeric scan에서 추가된 항목이다. 이 항목까지 기록한 상태로 T1 **COMPLETE** 판정은 유지한다.

### T2 — success-permitting `except` clauses

분류 단위는 각 `except` line이다. 같은 function에서 동일 결과를 내는 line만 한 행에 묶었다. `value/output`은 exception 뒤 caller가 보는 값 또는 남는 상태다.

T2 triage는 별도 표기가 없으면 **CLEAR**이다. physics/numerics 또는 live Fluent 의미를 결정해야 하는 행만 **NEEDS-ASTRA** 또는 **UNKNOWN**을 명시한다.

#### Batch 1 — three Fluent API probes + `_tmp_cell_profile.py` (4 files, 34 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/_probe_add_boundary_layers.py:65` (`workflow_task_exists`) | `False` + warning | **FALLBACK-OK** — probe-only |
| `scripts/_probe_add_boundary_layers.py:78` (`_jsonable`) | `repr`/recursive JSON-safe default | **FALLBACK-OK** |
| `scripts/_probe_add_boundary_layers.py:94,100` (`_read_attr`) | failed candidate ignored; final `None` | **FALLBACK-OK** — introspection probe |
| `scripts/_probe_add_boundary_layers.py:109,113` (`_argument_child`) | alternate access, then `None` | **FALLBACK-OK** |
| `scripts/_probe_add_boundary_layers.py:196` (`main`) | cleanup failure only; prior exit code survives | **FALLBACK-OK** |
| `scripts/_probe_add_boundary_layers.py:203` (`<main>`) | prints `PROBE FAILED`, `sys.exit(1)` | **FATAL-CORRECT** |
| `scripts/_probe_improve_task.py:59,92,111,118,129,133,284,291` | 위 probe와 각각 동일: `False`; JSON-safe fallback; final `None`; cleanup warning; top-level `sys.exit(1)` | **FALLBACK-OK** for `:59-284`; **FATAL-CORRECT** for `:291` |
| `scripts/_probe_periodic_after_surface.py:68,82,101,108,119,123,286,293` | 위 probe와 각각 동일: `False`; JSON-safe fallback; final `None`; cleanup warning; top-level `sys.exit(1)` | **FALLBACK-OK** for `:68-286`; **FATAL-CORRECT** for `:293` |
| `scripts/_tmp_cell_profile.py:166` (`collect_boundary_zones`) | 해당 boundary type을 건너뛴 partial dict | **FALLBACK-OK** — throwaway diagnostic이나 warning이 없어 completeness는 보장 안 됨 |
| `scripts/_tmp_cell_profile.py:290` (`create_x_range_iso_clip`) | settings/TUI fallback 모두 실패 후 re-raise | **FATAL-CORRECT** |
| `scripts/_tmp_cell_profile.py:379,386` (`iso_clip_segment_metrics`) | cleanup object가 남고 metric은 이미 계산됨; warning | **FALLBACK-OK** |
| `scripts/_tmp_cell_profile.py:893,900,993,998` (`main`) | report/surface/session cleanup 실패만 warning; prior result/CSV 유지 | **FALLBACK-OK** |
| `scripts/_tmp_cell_profile.py:1002` (`main`) | original cwd 복구 실패를 무시; output 유지 | **FALLBACK-OK** |
| `scripts/_tmp_cell_profile.py:1009` (`<main>`) | prints `SCRIPT FAILED`, `sys.exit(1)` | **FATAL-CORRECT** |

#### Batch 2 — reduction probe + three transcript scratch analyses (4 files, 32 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/_tmp_probe_reduction.py:145` (`collect_boundary_zones`) | failed type omitted; partial dict | **FALLBACK-OK** — probe-only |
| `scripts/_tmp_probe_reduction.py:270` (`try_sum_if`) | records `FAIL`, returns `None` | **FALLBACK-OK** — explicit diagnostic result |
| `scripts/_tmp_probe_reduction.py:284` (`print_pyfluent_versions`) | version string becomes `<unavailable: ...>` | **FALLBACK-OK** |
| `scripts/_tmp_probe_reduction.py:301,348` (`probe_report_type_allowed_values`) | pre-delete/cleanup failure warning; object may remain | **FALLBACK-OK** |
| `scripts/_tmp_probe_reduction.py:319` (`probe_report_type_allowed_values`) | inner error re-raised to outer `:337`, which records `FAIL` | **FALLBACK-OK** — probe result explicit |
| `scripts/_tmp_probe_reduction.py:334,337` (`probe_report_type_allowed_values`) | non-iterable prints raw; outer failure records `FAIL` and continues | **FALLBACK-OK** |
| `scripts/_tmp_probe_reduction.py:363,371,394,399` (`try_create_iso_clip_settings`) | delete/attribute/readback failure is printed; requested object/state may be partial | **FALLBACK-OK** — probe intentionally explores API |
| `scripts/_tmp_probe_reduction.py:486` (`main`) | setup failure returns `1` | **FATAL-CORRECT** |
| `scripts/_tmp_probe_reduction.py:506` (`main`) | zone resolution fields stay empty/`None`; later probe steps record `FAIL`/skip | **FALLBACK-OK** — explicit probe degradation |
| `scripts/_tmp_probe_reduction.py:606,662,665,693,716,797,820,843,864` (`main`) | relevant probe row remains `None`/records `FAIL`; script later returns `0` | **FALLBACK-OK** — diagnostic script reports per-step outcome, not campaign gate |
| `scripts/_tmp_probe_reduction.py:640` (`main`) | `clip_ready=False`, then TUI fallback | **FALLBACK-OK** |
| `scripts/_tmp_probe_reduction.py:773` (`main`) | plane row with all metric fields `None` is appended; next plane continues | **FALLBACK-OK** — row visibly marked by FAIL summary |
| `scripts/_tmp_probe_reduction.py:945,950,954` (`main`) | session cleanup warning / cwd restore ignored; prior output survives | **FALLBACK-OK** |
| `scripts/_tmp_probe_reduction.py:961` (`<main>`) | traceback + `sys.exit(1)` | **FATAL-CORRECT** |
| `scripts/analysis/nacl_analyze.py:7`; `scripts/analysis/nacl_decay.py:7`; `scripts/analysis/nacl_ptp.py:7` (`<module>`) | malformed transcript row is silently skipped; remaining rows drive output | **FALLBACK-OK** — scratch diagnostic, but partial-row count has no warning |

#### Batch 3 — last scratch analysis, backfills, meshing driver (4 files, 5 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/analysis/period.py:7` (`<module>`) | malformed row skipped; remaining rows drive diagnostic | **FALLBACK-OK** — scratch analysis |
| `scripts/backfill_mesh_manifest_fields.py:166` (`main`) | 해당 leaf는 unchanged; failure list에 추가 후 다음 leaf | **FATAL-CORRECT** — `:182-192`에서 any failure이면 exit `1` |
| `scripts/backfill_run_manifest_fields.py:192` (`main`) | 해당 leaf는 unchanged; failure list에 추가 후 다음 leaf | **FATAL-CORRECT** — `:208-218`에서 any failure이면 exit `1` |
| `scripts/batch_meshing.py:177` (`collect_session_failure_evidence`) | unreadable mesh log가 evidence에서 빠지고 record evidence만 남음 | **FATAL-CORRECT** — 이미 실패한 worker는 batch `FAILED`/exit `1`; 단, retry kind를 놓쳐 재시도하지 않을 수 있음(T4) |
| `scripts/batch_meshing.py:237` (`list_leftover_meshing_processes`) | `[]` + warning; settle가 leftover 없음으로 진행 | **FALLBACK-OK** — resource diagnostic degradation, case result 자체는 유지 |

#### Batch 4 — campaign drivers/inventory (4 files, 113 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/batch_postprocess_all_cases.py:309` (`read_inventory_csv`) | `([], error)`; `run:1577-1580`이 return `2` | **FATAL-CORRECT** |
| `scripts/batch_postprocess_all_cases.py:319,335` (`safe_read_json`, `int_from_any`) | `{}`/`None`; stage metadata가 blank/default로 남음 | **SILENT-BLANK** — current artifact validity를 약화시킴 |
| `scripts/batch_postprocess_all_cases.py:945` (`collect_report_failure_evidence`) | unreadable log가 evidence에서 빠짐; failed stage 자체는 유지 | **FALLBACK-OK** — transient retry만 놓칠 수 있음 |
| `scripts/batch_postprocess_all_cases.py:1042` (`execute_case`) | 세 stages 모두 `FAILED`, metric output 없음, error row 반환 | **SILENT-BLANK** — 명시적 FAILED row는 남지만 outer `run:1631-1645`가 항상 return `0` |
| `scripts/batch_postprocess_all_cases.py:1653` (`main`) | top-level return `2` | **FATAL-CORRECT** |
| `scripts/batch_report_extract.py:33,73,129,472` | layout/CSV/subprocess failure가 `None`/`False`/return code `-1` 및 FAILED row로 바뀜 | **SILENT-BLANK** — module 끝에 failure aggregate exit가 없어 process `0` |
| `scripts/batch_report_extract.py:524,607` | status CSV 또는 merged CSV write 실패; old aggregate가 남거나 새 파일 없음 | **SILENT-STALE** — warning만, process `0` |
| `scripts/batch_report_extract.py:564` | unreadable per-case CSV를 `continue`; merged output에서 row 누락 | **SILENT-BLANK** — process `0` |
| `scripts/batch_solver_rerun.py:323,1062` | invalid CLI/path가 exception으로 종료 | **FATAL-CORRECT** |
| `scripts/batch_solver_rerun.py:1240` | prior-success set이 비어 해당 case를 재실행 | **FALLBACK-OK** — conservative |
| `scripts/batch_solver_rerun.py:1449,1500` (`apply_residual_targets`) | `{}` 또는 partial `resulting_targets`; 못 바꾼 Fluent criteria가 남음 | **SILENT-STALE** — later assessment가 exact 5-equation set을 요구하지 않아 partial target으로 success 가능. **CLEAR**: missing/exception은 run fail로 하고 exact set+finite를 assert |
| `scripts/batch_solver_rerun.py:1529,1545,1553,1578,1597,1608,1640,1696,1713,1752,1796,1812,1848,1914,1943` | requested URF/coupling/pseudo-time setting은 미적용, 이전 Fluent value가 생존; WARN outcome만 기록하고 solve는 계속 가능 | **SILENT-STALE** — **NEEDS-ASTRA**: 각 requested numerical control이 strategy 필수인지 결정하고 필수이면 hard fail |
| `scripts/batch_solver_rerun.py:1592,1655,1730` | alternate `set_state`/name discovery fallback으로 이동; 최종 outcome이 성공 여부를 기록 | **FALLBACK-OK** |
| `scripts/batch_solver_rerun.py:1989,1997,2064,2080` | object-name/diagnostic subtree가 empty/error string; solver result에는 영향 없음 | **FALLBACK-OK** |
| `scripts/batch_solver_rerun.py:2105,2130,2134,3328` | residual/report/transcript snapshot 일부가 empty; remaining sources만 assessment에 들어감 | **SILENT-BLANK** — exact equation/report set이 없어 partial evidence가 current로 보일 수 있음 |
| `scripts/batch_solver_rerun.py:2148,2219,2226,2235,2245,2251` | TUI/allowed-values candidate 실패 후 다음 candidate 또는 unconstrained candidate list 사용 | **FALLBACK-OK** — final discretization readback이 별도 있음 |
| `scripts/batch_solver_rerun.py:2171,2179` | discretization read 실패를 `RuntimeError`로 re-raise | **FATAL-CORRECT** |
| `scripts/batch_solver_rerun.py:2301,2311,2321,2339,2358,2405,2450,2599,2702,2777,2876,3117,3196,3241` | set/read candidate 실패가 unconfirmed/error state로 남고 required first/restore checks가 success를 막음 | **FATAL-CORRECT** — alternate attempts 자체는 계속됨 |
| `scripts/batch_solver_rerun.py:2654,2666,2941,2958,2966,2976,3002,3011,3042,3052` | pseudo-time scale/HOTR/blending 요청이 미적용 또는 unreadable; 이전 value와 WARN state로 solve 계속 가능 | **SILENT-STALE** — **NEEDS-ASTRA**: optional aid인지 strategy identity의 필수 조건인지 결정 |
| `scripts/batch_solver_rerun.py:2724,2799` | aggregate post-set readback이 empty/error이나 per-key confirmed outcomes는 남음 | **FALLBACK-OK** — per-key required gate 사용 |
| `scripts/batch_solver_rerun.py:3310,4577,5018,5030,5041,5143,5229` | iterate/strategy/launch/switch/read/save failure가 typed exception으로 승격 | **FATAL-CORRECT** |
| `scripts/batch_solver_rerun.py:4017,4068,4316,4353` | final discretization becomes `{error: ...}`; restore confirmation false | **FATAL-CORRECT** |
| `scripts/batch_solver_rerun.py:5035,5050,5256,5262` | failed/session cleanup만 warning; result status 유지 | **FALLBACK-OK** |
| `scripts/batch_solver_rerun.py:5272` | `(False, error)`로 unsafe report worker 실행을 막음 | **FATAL-CORRECT** for optional report stage |
| `scripts/batch_solver_rerun.py:5477,5701,5717,5735,5802` | explicit `FAILED_*` record 반환; promotion 안 함 | **FATAL-CORRECT** — `main:6147-6150`에서 non-dry failure가 있으면 exit `1` |
| `scripts/batch_solver_rerun.py:6060` | input/path error return `2` | **FATAL-CORRECT** |
| `scripts/case_inventory.py:413` | malformed/unreadable batch config 대신 `_DEFAULT_MAX_ITER_FALLBACK`; report target가 actual run과 달라질 수 있음 | **SILENT-STALE** — effective threshold provenance가 바뀜 |
| `scripts/case_inventory.py:560` | default는 re-raise; `--skip-unreadable-manifests`이면 해당 exact case `[]`/broad scan skip | **FATAL-CORRECT** default; opt-in **SILENT-BLANK** |
| `scripts/case_inventory.py:736` | mtime `0.0`; 다른/older log가 latest로 선택될 수 있음 | **SILENT-STALE** |
| `scripts/case_inventory.py:744,762` | unreadable log는 binary/empty text로 처리되어 error evidence가 사라짐 | **SILENT-BLANK** |
| `scripts/case_inventory.py:803,1426` | parse field becomes `None` | **SILENT-BLANK** — 해당 optional status/count만 비어 있음 |
| `scripts/case_inventory.py:1234,1236,1238` | summary header/row `{}` + error; summary-derived metrics blank | **SILENT-BLANK** — inventory process는 계속 `0` |
| `scripts/case_inventory.py:1343` | `layout_record=None`; evaluation-window dP가 blank이나 다른 3 checks만으로 quality 판정 | **SILENT-BLANK** — missing layout을 campaign-ready로 허용하지 말 것 |
| `scripts/case_inventory.py:1410` | stage JSON `{}` + error; corresponding status fields blank | **SILENT-BLANK** |
| `scripts/case_inventory.py:2312` | top-level return `2` | **FATAL-CORRECT** |

Batch 4 핵심 finding: `batch_postprocess_all_cases.py`와 `batch_report_extract.py`는 per-case FAILED를 기록해도 parent exit code가 `0`일 수 있다. 두 driver 모두 any failed/validation/write failure에 nonzero를 반환해야 한다. **279 전 필수**.

#### Batch 5 — reference/CP/Yi diagnostics (4 files, 62 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/check_d2450_fresh_start_reference.py:105` (`compare_number`) | nonnumeric input becomes `within_tol=False`, diff `None` | **FALLBACK-OK** — visible `DRIFT` |
| `scripts/check_d2450_fresh_start_reference.py:207,211` (`_load_payloads`) | mesh/run payload `None` + warning; comparisons become `DRIFT`; script intentionally returns `0` | **FALLBACK-OK** — explicitly non-gating diagnostic |
| `scripts/diagnose_cp_face_filters.py:105` | candidate field failure saved as `last_error`; next candidate tried, all fail이면 raise | **FATAL-CORRECT** after exhausted fallback |
| `scripts/diagnose_cp_face_filters.py:212,227,256,279,544,578,731` | area `0.0`, wall list `[]`, CSV error field, or numeric result `nan`; diagnostic JSON/table에 degradation이 드러남 | **FALLBACK-OK** — diagnostic-only; **NEEDS-ASTRA**: `0.0`/`nan` diagnostic을 scientific decision에 허용할지 결정 |
| `scripts/diagnose_cp_face_filters.py:846,849,854,857` | transcript/session cleanup failure ignored; prior diagnostic output survives | **FALLBACK-OK** |
| `scripts/diagnose_cp_face_filters.py:876` | invalid root return `2` | **FATAL-CORRECT** |
| `scripts/diagnose_cp_face_filters.py:929` | per-run error recorded; any error yields final return `1` | **FATAL-CORRECT** |
| `scripts/diagnose_cp_face_filters.py:986` | missing `RO_DATA_ROOT` uses project `_scratch/diagnostics` output | **FALLBACK-OK** |
| `scripts/diagnose_cp_quantile_spread.py:117,166,231,361,452,514,565,624` | alternate field/report attempts; exhausted scalar becomes `None`/`nan` + error text; cleanup ignored | **FALLBACK-OK** — diagnostic-only; **NEEDS-ASTRA**: `nan` rows의 해석 사용 여부 결정 |
| `scripts/diagnose_cp_quantile_spread.py:683,686,691,694` | transcript/session cleanup failure ignored | **FALLBACK-OK** |
| `scripts/diagnose_cp_quantile_spread.py:814` | invalid root return `2` | **FATAL-CORRECT** |
| `scripts/diagnose_cp_quantile_spread.py:886` | per-run error JSON; all runs fail이면 return `1`, partial이면 return `0` | **FALLBACK-OK** — partial diagnostic is explicit in JSON |
| `scripts/diagnose_cp_quantile_spread.py:916` | output falls back to project `_scratch/diagnostics` | **FALLBACK-OK** |
| `scripts/diagnose_yi_saturation.py:166,180,193,213,258,303,308,350,362,367,378,391,423,480,495,621` | unavailable area/zone/reduction becomes `0.0`, `[]`, partial dict/error or unconditional bbox fallback | **FALLBACK-OK** — diagnostic-only; output carries notes/errors except some cleanup passes |
| `scripts/diagnose_yi_saturation.py:692,700,705,714,721` | reconstruction load/TUI/read failures accumulate `load_note`/`error`; transcript cleanup ignored | **FALLBACK-OK** |
| `scripts/diagnose_yi_saturation.py:956,1043,1046,1051,1054` | report/surface/session cleanup failure ignored | **FALLBACK-OK** |
| `scripts/diagnose_yi_saturation.py:1133` | invalid root return `2` | **FATAL-CORRECT** |
| `scripts/diagnose_yi_saturation.py:1179` | per-run error recorded; any error yields final return `1` | **FATAL-CORRECT** |
| `scripts/diagnose_yi_saturation.py:1206` | output falls back to project `_scratch/diagnostics` | **FALLBACK-OK** |

#### Batch 6 — summary/meshing worker/CFF probe (4 files, 35 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/make_summary_figures.py:440` (`_draw_heatmap`) | color calculation failure 시 `txt_color="black"`; figure는 생성됨 | **FALLBACK-OK** — 표시 가독성만 저하 |
| `scripts/meshing_code_260616.py:145` (`read_pyfluent_watchdog_err`) | unreadable watchdog file becomes `""`; run record에서 watchdog evidence 누락 | **FALLBACK-OK** — mesh success/failure status와 무관한 보조 evidence |
| `scripts/meshing_code_260616.py:175,184,192,206` (`teardown_meshing_session`) | graceful exit 실패 시 `force_exit`; 확인/강제 종료까지 실패하면 `"unresolved"` 또는 `finished=None` | **FALLBACK-OK** — artifact status는 이미 정해졌고 resource cleanup degradation이 출력됨 |
| `scripts/meshing_code_260616.py:244,486,1152` | invalid overrides, 기존 mesh 삭제 실패, meshing failure를 re-raise | **FATAL-CORRECT** |
| `scripts/meshing_code_260616.py:558` (`get_available_labels`) | `[]`; requested-label pre-check를 건너뛰고 Fluent task 실행 | **FALLBACK-OK** — 잘못된 label이면 downstream task가 fail; warning이 남음 |
| `scripts/meshing_code_260616.py:593` (`workflow_task_exists`) | `False`; insertion/second existence check로 이동 | **FALLBACK-OK** — insertion 또는 verification failure는 uncaught/fatal |
| `scripts/meshing_code_260616.py:1162,1170,1209` | transcript stop/continuation merge/cwd restore failure ignored | **FALLBACK-OK** — log 일부와 caller cwd만 영향; primary mesh/manifest status는 유지 |
| `scripts/meshing_code_260616.py:1201` | mesh run record write failure 시 record 없음 또는 이전 record가 남고 worker exit는 성공 가능 | **SILENT-STALE** — mesh와 manifest는 이미 작성되지만 ledger evidence가 current인지 gate가 확인하지 않음. **CLEAR**: successful mesh에서 record write failure를 fatal로 승격하고 atomic replace 사용 |
| `scripts/print_cp_definition_table.py:39` (`_as_float`) | invalid scalar becomes `None`, table cell becomes `-` | **FALLBACK-OK** — print-only diagnostic이고 missing이 가시적 |
| `scripts/probe_cff_surface_areaavg.py:97` (`collect_membrane_walls`) | discovery failure 시 fixed fallback `["wall_top_mem", "wall_bottom_mem"]` | **UNKNOWN** — 실제 zone names와 fallback 적합성은 live Fluent case가 필요 |
| `scripts/probe_cff_surface_areaavg.py:124,135` | invalid CSV scalar는 raw string, CFF-name listing failure는 `[]`+error text | **FALLBACK-OK** — diagnostic step가 FAIL로 보이고 step 3 미통과 시 exit `1` |
| `scripts/probe_cff_surface_areaavg.py:249,261,265,273,275,287,292,294` | CFF load/define candidate 실패를 error list에 쌓고 다음 API candidate 시도; all fail이면 `(False, errors)` | **FALLBACK-OK** — explicit probe fallback chain |
| `scripts/probe_cff_surface_areaavg.py:425` | step 3 becomes `{pass: False, error: ...}` and main exits `1` | **FATAL-CORRECT** |
| `scripts/probe_cff_surface_areaavg.py:449` | first UDM field spelling 실패 시 `cm_token`으로 재시도; second failure is uncaught | **FATAL-CORRECT** after fallback |
| `scripts/probe_cff_surface_areaavg.py:516` | step 5 becomes FAIL but step 3가 이미 PASS이면 main exits `0` | **FALLBACK-OK** — 이 probe의 documented gate는 step 3뿐; **NEEDS-ASTRA**: step 5 scientific comparison도 process gate여야 하는지 결정 |
| `scripts/probe_cff_surface_areaavg.py:562,570,573,578,581` | temporary report/session cleanup failure ignored; diagnostic result survives | **FALLBACK-OK** |

#### Batch 7 — numerics probe/PyEnSight/field check (4 files, 215 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/probe_species_numerics_context.py:92` (`DictKeyLeafAdapter.__setattr__`) | item assignment 실패 시 `set_state`로 재시도; second failure는 uncaught | **FATAL-CORRECT** after fallback |
| `scripts/probe_species_numerics_context.py:116,271,314,350` | unavailable getter는 `None`; explicit/implicit write는 `ERROR` matrix row; probe는 그래도 exit `0` | **FALLBACK-OK** — diagnostic matrix에 failure가 명시됨; campaign gate가 아님 |
| `scripts/pyensight_contour_export.py:52` | import error string 저장; real run은 `:4815-4818`에서 exit `2` | **FATAL-CORRECT** |
| `scripts/pyensight_contour_export.py:712` | invalid range를 `ValueError`로 재발생; main exit `3` | **FATAL-CORRECT** |
| `scripts/pyensight_contour_export.py:850,866` (`find_surfaces`) | part listing failure는 `[]`; midplane lookup failure는 first 10 parts fallback + warning | **FATAL-CORRECT/FALLBACK-OK** — empty는 FAILED, fallback은 WARN이고 aggregate exit `1` |
| `scripts/pyensight_contour_export.py:921` (`find_ensight_variable`) | `(None,None)`; requested field record가 FAILED가 됨 | **FATAL-CORRECT** — aggregate exit `2` |
| `scripts/pyensight_contour_export.py:964,985,1012,1019,1025,1035,1147,1252,1357,1399,1410,1419,1425,1444` | bounds/introspection value가 `None`/`[]`/error entry로 축소되고 다른 candidate를 시도 | **FALLBACK-OK** — diagnostic 또는 later bounds-fit status에 드러남 |
| `scripts/pyensight_contour_export.py:1594,1633,1650,1668,1670,1680` | palette lookup가 `None`; range warning으로 전파 | **FALLBACK-OK** — exported record가 WARN이 되어 aggregate exit `1` |
| `scripts/pyensight_contour_export.py:1754,1760,1774,1801,1805` | alternate auto/fixed range API를 시도; 실패 시 warning과 `RANGE_MODE_NONE` | **FALLBACK-OK** — WARN record/exit `1` |
| `scripts/pyensight_contour_export.py:1799` | `palette.MINMAX` readback failure를 requested min/max가 적용된 것처럼 `RANGE_MODE_FIXED`와 empty warning으로 반환 | **SILENT-STALE** — 실제 palette value가 불명인데 figure/status는 SUCCESS 가능. **UNKNOWN**: live PyEnSight readback fault가 set 성공을 의미하는지 확인 필요; 최소 수정은 readback failure를 WARN으로 기록 |
| `scripts/pyensight_contour_export.py:1830,1841,1911,1919,1927,1939,1947,1954,1964,2007,2018,2023,2048,2053,2169,2183,2192,2209` | label/legend discovery와 설정 일부가 실패해 default/unchanged style이 남고 status/error metadata로 반환 | **FALLBACK-OK** — figure content metric이 아닌 presentation degradation |
| `scripts/pyensight_contour_export.py:2248,2255,2263,2272,2277,2283,2292,2294,2300,2315,2323,2360,2370,2391,2444,2478,2482` | scene/camera API candidate 실패 시 alternate method 또는 warning diagnostic 사용 | **FALLBACK-OK** — bounds-fit/readback metadata에 결과가 남음 |
| `scripts/pyensight_contour_export.py:2509,2561,2580,2591,2598` | export-option inspection/debug-sweep가 default/partial diagnostics로 축소 | **FALLBACK-OK** — debug-only |
| `scripts/pyensight_contour_export.py:2645,2653,2661,2669` | case load 3 candidates를 순차 시도; all fail이면 error 반환, main exit `2` | **FATAL-CORRECT** after fallback |
| `scripts/pyensight_contour_export.py:2709,2719,2724,2805,2822,2834,2847,2852,2874,2894,2953,2961,2997,3020` | primitive/volume/clip/average lookup가 `None`/`[]`/error가 되어 fallback path 또는 warning으로 이동 | **FALLBACK-OK** — derived figure가 fallback이면 WARN/exit `1`; hard absence는 FAILED |
| `scripts/pyensight_contour_export.py:3087,3112,3162,3253,3294,3344,3383` | derived calculator/pressure lookup 실패가 `(None,...error)`; raw field fallback과 warning 또는 FAILED | **FALLBACK-OK** — error가 record에 남고 WARN/FAILED는 nonzero |
| `scripts/pyensight_contour_export.py:3411,3422,3440,3504` | prior PyFluent CSV/JSON reference becomes `None`+error | **FALLBACK-OK** — selected fallback/reference mode와 warning metadata로 남음 |
| `scripts/pyensight_contour_export.py:3526,3529,3541` | variable inventory attr becomes `""`, payload becomes error row, write returns `write_failed:*` | **FALLBACK-OK** — debug inventory only |
| `scripts/pyensight_contour_export.py:3984,4007,4014` | derivation/visibility failure는 warning 또는 WARN record; fallback field/part 사용 | **FALLBACK-OK** — aggregate exit `1` |
| `scripts/pyensight_contour_export.py:4000,4005` | individual part visibility assignment failure ignored; previous visibility survives | **SILENT-STALE** — unrelated/target parts가 잘못 보이는 figure가 SUCCESS 가능. **CLEAR**: per-part failure를 warnings에 추가 |
| `scripts/pyensight_contour_export.py:4031` | pre-palette snapshot stays empty | **FALLBACK-OK** — palette lookup strategies continue |
| `scripts/pyensight_contour_export.py:4039,4043,4316,4320` | selection/color/export failure가 FAILED record; restore failure만 ignored | **FATAL-CORRECT** — aggregate exit `2` |
| `scripts/pyensight_contour_export.py:4088` | auto-range readback fails: metadata min/max stay `None`, image can remain SUCCESS | **SILENT-BLANK** — info text만 있고 status는 SUCCESS. **CLEAR**: record를 WARN으로 승격하거나 finite min/max를 필수화 |
| `scripts/pyensight_contour_export.py:4146,4305,4334` | initial fit/bounds diagnostics/debug sweep failure ignored or info-only; later primary fit/image remains | **FALLBACK-OK** |
| `scripts/pyensight_contour_export.py:4342` | post-export visibility restore failure ignored; stale visibility can affect next field | **SILENT-STALE** — later figure가 wrong parts를 보이면서 SUCCESS 가능. **CLEAR**: restore failure를 next record warning 또는 session reset으로 처리 |
| `scripts/pyensight_contour_export.py:4443,4467,4484` (`save_colorbar_metadata`) | JSON/TXT/CSV 중 실패한 파일은 missing 또는 old; 일부 하나만 쓰여도 records는 `colorbar_metadata_written=True` | **SILENT-STALE** — process exit와 무관. **CLEAR**: requested metadata set 전체의 atomic success를 검사 |
| `scripts/pyensight_contour_export.py:4650` | stdout/stderr encoding unchanged | **FALLBACK-OK** |
| `scripts/pyensight_contour_export.py:4662,4670,4677,4693,4893` | input/config/path/outer failure가 exit `3` | **FATAL-CORRECT** |
| `scripts/pyensight_contour_export.py:4878` | per-item exception becomes FAILED record; final aggregate exit `2` | **FATAL-CORRECT** |
| `scripts/pyensight_contour_export.py:4902` | session close warning; records/exit unchanged | **FALLBACK-OK** |
| `scripts/pyensight_extra_figures.py:89` | PyEnSight import error 저장; `open_case_in_pyensight` error로 run exit `1` | **FATAL-CORRECT** |
| `scripts/pyensight_extra_figures.py:102` | PIL import error 저장; standalone colorbars는 `planned_exports`에 넣기 전에 전부 skip, 다른 figure가 있으면 exit `0` | **SILENT-BLANK** — requested colorbar set가 사라짐. **CLEAR**: requested jobs를 먼저 plan하고 missing PIL을 failed planned exports로 처리 |
| `scripts/pyensight_extra_figures.py:404,561` | invalid overrides/range를 `ValueError`; main exit `1` | **FATAL-CORRECT** |
| `scripts/pyensight_extra_figures.py:636,643,652` | fluid parts/variable lookup becomes `[]`/`None` | **SILENT-BLANK** — 일부 requested fields는 plan 자체에서 빠져 다른 figures만으로 exit `0` 가능. **CLEAR**: requested output list를 discovery 전에 만들고 missing을 failed로 count |
| `scripts/pyensight_extra_figures.py:680,714,721,756` | inventory/traversal/extents candidate가 partial/empty | **FALLBACK-OK** — diagnostic/alternate candidate only |
| `scripts/pyensight_extra_figures.py:837,842,849,854,863,888,892,912,924,940,953,972,985,989,997` | scene/color/legend/view/readback failure ignored or alternate API used; existing figure export continues | **FALLBACK-OK** — presentation-only degradation; warnings are incomplete |
| `scripts/pyensight_extra_figures.py:1010` (`export_png`) | `False`; caller가 이미 planned output으로 등록해 final exit `1` | **FATAL-CORRECT** |
| `scripts/pyensight_extra_figures.py:1044,1054,1082,1104,1116,1135,1145,1149,1173,1189,1205,1231,1270,1274` | slice/palette/colorbar style은 primitive/default font/color fallback | **FALLBACK-OK** — presentation-only |
| `scripts/pyensight_extra_figures.py:1324` | colorbar render `False`; 이미 planned이면 final exit `1` | **FATAL-CORRECT** |
| `scripts/pyensight_extra_figures.py:1392,1406,1471,1482,1503` | Q scene/style/title setting skipped/default | **FALLBACK-OK** — presentation-only |
| `scripts/pyensight_extra_figures.py:1537,1558,1606` | requested x/z/iso part creation becomes `None` before output is added to `planned_exports` | **SILENT-BLANK** — 해당 requested figure가 누락돼도 other figures로 exit `0` 가능. **CLEAR**: plan filenames before part creation and count `None` as failure |
| `scripts/pyensight_extra_figures.py:1627,1646` | invalid/unreadable Q thresholds are skipped or fallback sweep selected | **SILENT-BLANK** — configured threshold 일부가 plan에서 사라질 수 있음. **CLEAR**: config validation에서 every threshold finite numeric assert |
| `scripts/pyensight_extra_figures.py:1672,1678,1688,1716,1744,1755` | velocity/vector/calculator candidate returns `None` or continues | **SILENT-BLANK** — derived requested figure가 plan에서 빠질 수 있음; requested-output contract로 gate 필요 |
| `scripts/pyensight_extra_figures.py:1858,1862` | 3 case-load candidates; all fail이면 error, run exit `1` | **FATAL-CORRECT** after fallback |
| `scripts/pyensight_extra_figures.py:1967` | invalid displayed threshold is printed as ignored; actual resolver도 invalid entry를 skip | **SILENT-BLANK** — config 항목이 실행 없이 누락됨; early validation 필요 |
| `scripts/pyensight_extra_figures.py:2080,2148` | failed-session cleanup ignored; active-range failure returns `1` | **FALLBACK-OK/FATAL-CORRECT** |
| `scripts/pyensight_extra_figures.py:2296,2347` | Q/vorticity compute failure가 requested figures를 skip; Q는 `strict_qcriterion_required=True`만 fatal | **SILENT-BLANK** — non-strict mode에서는 other figures로 exit `0`; requested-output contract 필요 |
| `scripts/pyensight_extra_figures.py:2476` | threshold PNG은 성공했지만 generic copy 실패; 이전 generic file이 남거나 없음, exit는 `0` 가능 | **SILENT-STALE** — generic output을 planned set에 넣고 copy failure를 nonzero로 처리 |
| `scripts/pyensight_extra_figures.py:2547` | session close warning | **FALLBACK-OK** |
| `scripts/pyensight_extra_figures.py:2562` | invalid config/path return `1` | **FATAL-CORRECT** |
| `scripts/pyfluent_field_check.py:173,183` | exotic `pd.isna` failure는 valid 취급; nonnumeric scalar는 `None` | **FALLBACK-OK** — later numeric checks omit value rather than invent number |
| `scripts/pyfluent_field_check.py:310` | summary CSV read failure adds FAIL; final exit `2` | **FATAL-CORRECT** |
| `scripts/pyfluent_field_check.py:455` | raw JSON unreadable adds WARN and returns; default process exit remains `0` | **SILENT-BLANK** — `--fail-on-warn`만 검출. **CLEAR**: required raw report JSON read failure를 FAIL로 분류 |
| `scripts/pyfluent_field_check.py:513,548` | PyFluent import/unhandled live error adds FAIL; final exit `2` | **FATAL-CORRECT** |
| `scripts/pyfluent_field_check.py:531` | `switch_to_solver()` failure 후 current session으로 계속, WARN | **UNKNOWN** — live Fluent에서 meshing session이 subsequent solver checks를 정상 제공하는지 확인 필요; `--fail-on-warn` 없이는 exit `0` 가능 |
| `scripts/pyfluent_field_check.py:557` | session-exit warning | **FALLBACK-OK** |
| `scripts/pyfluent_field_check.py:649` | case/data read API candidates; all fail이면 FAIL record | **FATAL-CORRECT** |
| `scripts/pyfluent_field_check.py:688,716,722` | surface/scalar/vector introspection failure는 WARN/empty set; default exit `0` 가능 | **SILENT-BLANK** — live validation 자체가 수행되지 않아도 `--fail-on-warn` 없으면 성공. **CLEAR**: requested `--with-fluent` introspection failure를 FAIL로 분류 |
| `scripts/pyfluent_field_check.py:1024` | outer error return `3` | **FATAL-CORRECT** |

#### Batch 8 — report/shear extract and mesh rebuild tools (4 files, 101 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/pyfluent_report_extract.py:22` | non-Jupyter environment에서는 local `display()` printer 사용 | **FALLBACK-OK** |
| `scripts/pyfluent_report_extract.py:303,316,335` | missing layout/window와 invalid override JSON은 `ValueError`로 종료 | **FATAL-CORRECT** |
| `scripts/pyfluent_report_extract.py:433,442,451,478,500` | PyFluent name API candidates 실패 시 `[]`/zone type skip | **FATAL-CORRECT** for required zones — later inlet/outlet/membrane/fluid checks가 raise; optional zone inventory만 blank |
| `scripts/pyfluent_report_extract.py:556` | old report delete 실패 시 `False`; subsequent create/update API가 old object를 재사용할 수 있음 | **UNKNOWN** — old report state가 overwrite되는지는 live Fluent object semantics 필요; 최소 수정은 definition state readback 검증 |
| `scripts/pyfluent_report_extract.py:709,717,731` | z-plane Settings/TUI candidates; all fail이면 `RuntimeError` | **FATAL-CORRECT** after fallback |
| `scripts/pyfluent_report_extract.py:1226,1250` | report definition missing + `failed_report_specs`; load-bearing set만 `require_load_bearing_report_definitions`가 reject | **SILENT-BLANK** for non-load-bearing reports — later output가 `None`/missing인데 exit `0` 가능. **CLEAR**: campaign artifact contract에 required report set를 명시하고 exact set 검사 |
| `scripts/pyfluent_report_extract.py:1300` | compute failure writes `computed_values[report_name]=None`와 error payload; flux/load-bearing만 re-raise | **SILENT-BLANK** for optional reports — summary blanks가 process success 가능; current load-bearing/mass-closure path는 **FATAL-CORRECT** |
| `scripts/pyfluent_report_extract.py:1360` | concentration diagnostic values retain initialized `None`; error columns are filled, process continues | **SILENT-BLANK** — no campaign gate rejects missing diagnostic values; **NEEDS-ASTRA**: 이 diagnostic이 campaign-required인지 결정 |
| `scripts/pyfluent_report_extract.py:1417,1454,1489,1659` | plane/field candidates are retried; all canonical path failures raise | **FATAL-CORRECT** after fallback |
| `scripts/pyfluent_report_extract.py:1593,1595` | center-bulk diagnostic becomes error string/legacy fallback value | **FALLBACK-OK** — explicitly diagnostic, canonical path와 분리됨 |
| `scripts/pyfluent_report_extract.py:2456` | CSV/JSON은 current지만 run manifest의 `convergence_quality`는 missing 또는 previous value | **SILENT-STALE** — exit `0`, manifest freshness gate 없음. **CLEAR**: manifest update를 required atomic finalization으로 승격 |
| `scripts/pyfluent_report_extract.py:2497` | outer exception re-raise | **FATAL-CORRECT** |
| `scripts/pyfluent_report_extract.py:2509` | `report_extract_timing.json` missing 또는 old, process success | **SILENT-STALE** — performance metadata only; campaign 전 필수 아님 |
| `scripts/pyfluent_report_extract.py:2519,2525,2531,2537` | transcript/session/cwd cleanup warning; primary artifacts/status 유지 | **FALLBACK-OK** |
| `scripts/pyfluent_shear_contour_export.py:222,230,236,245,299,326` | name/zone/field discovery becomes partial or `[]`; no usable source이면 side failure | **FATAL-CORRECT** when all fail; selected subset의 completeness는 아래 field-data finding 참조 |
| `scripts/pyfluent_shear_contour_export.py:386,396,406,414` | diagnostic text/attrs/state/call becomes fallback string 또는 `(False,error)` | **FALLBACK-OK** — diagnostic/candidate probing |
| `scripts/pyfluent_shear_contour_export.py:503,519,533,542,560,578,602,630,654,688,720,756` | Settings/TUI CFF candidates 실패를 error state에 모으고 alternate path/fallback export 시도 | **FALLBACK-OK** — all source paths fail이면 final FAIL/exit `2` |
| `scripts/pyfluent_shear_contour_export.py:1151,1170,1193,1201,1219,1247,1401,1469,1488,1497,1507,1516,1537,1821` | scene/view/display setting becomes false/default/diagnostic error while image may still export | **FALLBACK-OK** — presentation degradation is status payload에 대체로 남음 |
| `scripts/pyfluent_shear_contour_export.py:1895,1918,1932,1948` | legend hide API candidates fail; prior legend visibility survives with diagnostic state | **FALLBACK-OK** — presentation-only; status payload carries failure |
| `scripts/pyfluent_shear_contour_export.py:2000,2003,2010` | auto range readback becomes `(None,None,"auto_unavailable")`; image can still final `OK` | **SILENT-BLANK** — colorbar metadata lacks range but process exit `0` possible. **CLEAR**: requested colorbar/range metadata unavailable를 WARN status로 승격 |
| `scripts/pyfluent_shear_contour_export.py:2036,2058` | optional post-mask returns `(False,"",error)` | **FALLBACK-OK** — requested method failure is status payload에 남고 primary image survives |
| `scripts/pyfluent_shear_contour_export.py:2109` | native CFF becomes failed tuple; configured fallback mode이면 field-data route로 이동 | **FALLBACK-OK** — all sides fail이면 exit `2` |
| `scripts/pyfluent_shear_contour_export.py:2144,2225,2246` | temporary CFF cleanup/render refresh/scene reapply failure ignored 또는 diagnostic status | **FALLBACK-OK** — primary image write gate remains |
| `scripts/pyfluent_shear_contour_export.py:2262,2266` | Settings picture write 후 TUI fallback; both fail이면 `RuntimeError` | **FATAL-CORRECT** |
| `scripts/pyfluent_shear_contour_export.py:2396,2419,2454,2456` | view debug sweep becomes setup error/partial candidate output | **FALLBACK-OK** — debug-only |
| `scripts/pyfluent_shear_contour_export.py:2501` | scalar-field candidate 실패 후 next candidate; none이면 fallback failure | **FATAL-CORRECT** after fallback |
| `scripts/pyfluent_shear_contour_export.py:2580,2590` | individual membrane zone의 values/centroids read failure는 `continue`; remaining zones로 image success 가능 | **SILENT-BLANK** — membrane side 일부가 빠진 contour가 `OK` 가능. **CLEAR**: requested zone exact set와 per-zone equal nonzero counts를 require |
| `scripts/pyfluent_shear_contour_export.py:2652` | triangulation failure 시 scatter fallback | **FALLBACK-OK** — plot type가 figure title/status에 명시됨 |
| `scripts/pyfluent_shear_contour_export.py:2785,2814,2831` | JSON/TXT/CSV metadata 중 실패한 파일은 missing/old; 하나만 쓰여도 `colorbar_metadata_written=True` | **SILENT-STALE** — exact metadata artifact set gate 필요 |
| `scripts/pyfluent_shear_contour_export.py:3302` | console encoding unchanged | **FALLBACK-OK** |
| `scripts/pyfluent_shear_contour_export.py:3323,3337,3913` | config/path/runtime failure becomes non-OK final status; exit `2`/`3` | **FATAL-CORRECT** |
| `scripts/pyfluent_shear_contour_export.py:3923,3929` | Fluent cleanup ignored/warn; final status preserved | **FALLBACK-OK** |
| `scripts/rebuild_mesh_ledger_from_logs.py:94` | invalid root return `2` | **FATAL-CORRECT** |
| `scripts/rebuild_mesh_manifest.py:145` | unreadable/missing manifest re-raises `ManifestError`; missing-required hint only | **FATAL-CORRECT**, but missing `manifest.json` 자체는 이 tool로 rebuild 불가(T4 dead end) |
| `scripts/rebuild_mesh_manifest.py:406` | per-leaf error를 failure list에 넣고 continue; final return `1` | **FATAL-CORRECT** |

#### Batch 9 — solver worker/residual and load-bearing validators (4 files, 83 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `scripts/residual_measurement_report.py:62` | `batch_config.py` load failure 시 hardcoded `max_iterations=1000`, `residual_target=1e-5`가 report에 들어감 | **SILENT-STALE** — warning만 있고 exit `0`; actual campaign settings와 다른 classification 가능. **CLEAR**: provenance load failure를 fatal 또는 explicit UNKNOWN으로 처리 |
| `scripts/residual_measurement_report.py:274` | outer error return `2` | **FATAL-CORRECT** |
| `scripts/solver_code_260616.py:113` | case-local UDF missing이면 current repo `udfs/<udf_version>`의 `analytic_cwall`을 사용 | **SILENT-STALE** — run 당시 UDF가 없는 상태에서 later repo copy가 manifest 값으로 기록될 수 있음. **CLEAR**: case-local UDF/hash를 required artifact로 만들고 fallback 제거 |
| `scripts/solver_code_260616.py:187,382,887` | invalid override/diffusivity/G token raises | **FATAL-CORRECT** |
| `scripts/solver_code_260616.py:528,535,542` (`update_solver_thread_names`) | 3 API 모두 실패해도 warning 후 solver 계속; old thread names/current UDF lookup state가 남음 | **UNKNOWN** — UDF source activation 영향은 live Fluent가 필요. **NEEDS-ASTRA**: membrane UDF execution의 required precondition이면 hard fail+marker gate 필요 |
| `scripts/solver_code_260616.py:617,626,635,662,688` | object/zone discovery becomes `[]`/partial | **FATAL-CORRECT** for required inlet/outlet/membrane/fluid zones — later explicit checks raise |
| `scripts/solver_code_260616.py:708,713,718` | object diagnostic attrs/state unavailable; setup continues | **FALLBACK-OK** — print-only diagnostic |
| `scripts/solver_code_260616.py:737,750,755,766` | transcript stat/read/glob failure excludes current file; older `fluent-*.trn` remains searchable | **SILENT-STALE** — old UDF marker/G 또는 stop evidence가 current로 통과 가능. **CLEAR**: launch 전에 attempt-specific transcript path/mtime boundary를 capture하고 그 파일만 gate |
| `scripts/solver_code_260616.py:991,997,1004,1015,1018,1048,1057,1074,1111,1163` | inlet Settings API probes/candidates fail then alternate allowed value/TUI path를 시도 | **FALLBACK-OK** until final TUI attempt |
| `scripts/solver_code_260616.py:1204` | TUI fallback failure를 print한 뒤에도 `Inlet BC set ...`를 출력하고 return; prior inlet state survives | **SILENT-STALE** — old transcript marker까지 재사용 가능해 zero/wrong velocity run이 성공 가능. **CLEAR**: TUI exception re-raise, current-attempt marker, inlet boundary readback/flux gate 추가. **279 전 필수** |
| `scripts/solver_code_260616.py:1281,1301,1307` | report pre/post state unavailable; optional flags retain defaults | **FALLBACK-OK** — required field/zone assignments themselves는 uncaught |
| `scripts/solver_code_260616.py:1328` | delete failure returns `False`; existing report definition may survive | **UNKNOWN** — following update가 all required leaf state를 overwrite하는지 live Fluent readback 필요; exact definition state gate 권장 |
| `scripts/solver_code_260616.py:1407,1417,1443,1453,1480,1488` | alternative report property assignments; final failure remains uncaught | **FATAL-CORRECT** after fallback |
| `scripts/solver_code_260616.py:1503` | `configure_report_definition_for_transcript` optional leaf failures ignored; prior report print/file settings survive | **SILENT-STALE** — QoI transcript/report evidence가 missing/stale일 수 있음. **NEEDS-ASTRA**: QoI stop enabled 시 어떤 leaf가 required인지 결정 후 readback gate |
| `scripts/solver_code_260616.py:1606` | solve-time report create failure after initialization raises | **FATAL-CORRECT** |
| `scripts/solver_code_260616.py:1717` | pressure QoI report update failure returns partial `created`; solver can continue with fewer pressure monitors | **SILENT-BLANK** — pressure convergence evidence 일부가 없음. **NEEDS-ASTRA**: enabled QoI stop에서 exact pressure report set가 필수인지 결정 |
| `scripts/solver_code_260616.py:1854,1860` | object-name API probing continues with partial/empty names | **FALLBACK-OK** — create/get fallback follows |
| `scripts/solver_code_260616.py:1881,1889,1893,1897,1901,1932,1936,1948,1959` | LMH report-file/convergence object API variants 또는 optional leaf writes fail silently; created object may retain prior/default state | **SILENT-STALE** — QoI condition가 requested configuration과 다르지만 residual/max-iteration solve는 성공 가능. **NEEDS-ASTRA**: QoI stop가 campaign run identity이면 exact readback을 hard gate |
| `scripts/solver_code_260616.py:1990` | QoI report file read error returns `(False,False,None)` | **FALLBACK-OK** — conservative: QoI met으로 오인하지 않음 |
| `scripts/solver_code_260616.py:2185,2206,2214,2249,2265,2273,2286` | numerical-control set/readback failure becomes outcome with previous Fluent value retained | **SILENT-STALE** — callers record/print warning but do not fail run. **NEEDS-ASTRA**: required solver-control set를 지정하고 confirmed exact set를 gate |
| `scripts/solver_code_260616.py:2325,2341,2390,2407,2422,2445,2488,2504,2537` | species/pressure pseudo-time and URF setup becomes WARN/SKIPPED outcome; old value survives, solve continues | **SILENT-STALE** — **NEEDS-ASTRA**: campaign strategy identity에 속하는 requested controls는 failure를 fatal로 승격 |
| `scripts/solver_code_260616.py:3664,3753` | iteration/outer failure re-raise | **FATAL-CORRECT** |
| `scripts/solver_code_260616.py:3686,3727` | stop-reason determination failure leaves `solver_stop_reason=None`; manifest finalization skipped but final case/data is written and process can exit `0` | **SILENT-BLANK** — run manifest에는 current `stop_reason`/`analytic_cwall`/G가 없음 또는 old. **CLEAR**: non-None validated stop reason를 final write 전 required gate로 만들기. **279 전 필수** |
| `scripts/solver_code_260616.py:3766,3772,3778,3784` | transcript/session/cwd cleanup warning; artifact verification still runs | **FALLBACK-OK** |
| `scripts/validate_load_bearing_summary_csvs.py:142` | unreadable leaf increments `fail_count`; final nonzero | **FATAL-CORRECT** |
| `src/ro/convergence_quality.py:54` | nonnumeric value becomes `None`; quality becomes `UNKNOWN`, not PASS | **FALLBACK-OK** — no false success in this classifier |

#### Batch 10 — core layout/report/manifest/mesh helpers (4 files, 31 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `src/ro/domain_layout.py:438` | unreadable replace log를 skip하고 다른 log의 first mesh path 또는 `None` 사용 | **SILENT-STALE** — filename sort상 older log가 current provenance로 선택될 수 있음. **CLEAR**: current attempt log를 명시하고 unreadable이면 fail; 여러 log에서는 newest mtime+run id require |
| `src/ro/fluent_report_helpers.py:35,44,53` | named-object API candidates all fail이면 `[]` | **FATAL-CORRECT** when caller requires objects; optional discovery는 blank/diagnostic |
| `src/ro/fluent_report_helpers.py:71,82,99,120,131,148` | x/z plane Settings/TUI candidates; all fail이면 `RuntimeError` | **FATAL-CORRECT** after fallback |
| `src/ro/fluent_report_helpers.py:242` (`resolve_channel_midplane_z_m`) | measured z bounds failure 시 supplied `fallback_z_m` (default `0.0`)을 사용하고 plane 생성은 성공 가능 | **SILENT-STALE** — measurement failure가 guessed coordinate로 바뀜. **CLEAR**: live solver path에서는 reduction failure를 fatal로 하고 explicit bounds만 offline fallback 허용. **279 전 필수** |
| `src/ro/fluent_report_helpers.py:689,706,1044,1055` | invalid/unresolved reduction location raises typed error | **FATAL-CORRECT** |
| `src/ro/fluent_report_helpers.py:1434,1438,1802,1807` | temporary reports/iso-clips cleanup failure ignored; computed output survives | **FALLBACK-OK** — resource cleanup only |
| `src/ro/fluent_report_helpers.py:1624` (`_area_frac_field_below`) | Fluent area-report compute exception becomes `sub_area=0.0`; quantile bisection treats it as a real zero-area sample | **SILENT-STALE** — wrong quantile/spread can look finite and current; no gate notices. **CLEAR**: exception re-raise or explicit invalid sample. `compute_cp_spread=True` 전에 필수 |
| `src/ro/fluent_report_helpers.py:1635,1639` | quantile temporary object cleanup failure ignored | **FALLBACK-OK** |
| `src/ro/manifest.py:499,523,534,582` | invalid location/read/write is wrapped as `ManifestError` | **FATAL-CORRECT** |
| `src/ro/manifest.py:824` (`_iter_child_dirs`) | directory listing failure becomes `[]`; entire family/geo/mesh subtree disappears with no warning | **SILENT-BLANK** — scan can exit `0` with cases omitted. **CLEAR**: propagate `OSError` or collect skipped-root errors and force nonzero. **279 전 필수** |
| `src/ro/manifest.py:874` | default는 invalid manifest re-raise; `skip_invalid=True`이면 warning+skip | **FATAL-CORRECT** default; opt-in **SILENT-BLANK** by explicit operator choice |
| `src/ro/mesh_common.py:231` | invalid canonical mesh-name inputs become `(None,"UNAVAILABLE")` | **FALLBACK-OK** — ledger provenance explicitly unavailable |
| `src/ro/mesh_common.py:558` | malformed logged scalar/list becomes `None` | **SILENT-BLANK** — reconstructed record field가 absent/default로 흐를 수 있음. **CLEAR**: required logged fields의 parse failure를 accumulated validation error로 승격 |
| `src/ro/mesh_common.py:780` | unreadable/stale `mesh_run_record.json` becomes `None` | **SILENT-BLANK** — caller가 missing과 corrupt를 구별 못하고 ledger/evidence를 생략 가능. **CLEAR**: typed read result `(payload,error)` 또는 strict mode 추가 |

#### Batch 11 — residual/shear guard/solver helpers (3 files, 19 clauses)

| file:line (`function`) | value/output | 분류 |
|---|---|---|
| `src/ro/residual_transcript.py:76` | unreadable transcript returns `(None,error)` | **SILENT-BLANK** — measurement row는 `PARSE_UNREADABLE`/null values지만 batch report process는 exit `0`; strict aggregate mode 필요 |
| `src/ro/residual_transcript.py:100,105,113` | malformed residual row becomes `None` and is skipped | **SILENT-BLANK** — explicit parse detail/count는 남지만 trailing current rows가 빠진 earlier window를 분석할 수 있음. **CLEAR**: malformed rows after recognized header를 parse failure로 gate |
| `src/ro/residual_transcript.py:183` | transcript directory listing failure becomes `[]`, then `PARSE_NO_TRANSCRIPT` null row | **SILENT-BLANK** — process exit `0`; strict report aggregate 필요 |
| `src/ro/residual_transcript.py:556` | summary CSV carry becomes all `None`, empty filename, `summary_metrics_wide_status=unreadable:*` | **SILENT-BLANK** — visible status지만 report exit `0` |
| `src/ro/residual_transcript.py:573` | expected numeric carry value가 raw string으로 남고 `summary_metrics_wide_status="ok"` | **SILENT-STALE** — invalid value가 typed success처럼 보임. **CLEAR**: numeric contract 실패 시 status invalid + null |
| `src/ro/residual_transcript.py:705` | unexpected per-case exception becomes null measurement record with `PARSE_UNREADABLE`; batch continues | **SILENT-BLANK** — report exit `0`; any non-`PARSE_OK`에 optional strict nonzero 추가 |
| `src/ro/shear_cff_mu_guard.py:33,41,82` | `.scm` parse/guard failure returns `None` 또는 INFO; mismatch check 자체가 skip됨 | **SILENT-BLANK** — wrong divisor가 undetected 상태로 figure path에 사용될 수 있음. **CLEAR**: CFF mode에서 parse failure를 WARN/failed status로 전파; logging callback failure만 FALLBACK-OK |
| `src/ro/solver_common.py:212,240` | malformed `common_solver_settings` values become fallback `max_iterations`/`residual_target` | **SILENT-STALE** — warning만, provenance report가 actual config와 달라질 수 있음. **CLEAR**: campaign path는 invalid settings를 fatal로 처리 |
| `src/ro/solver_common.py:306` | artifact size read failure를 `failures`에 추가하고 continue | **FATAL-CORRECT** |
| `src/ro/solver_common.py:394,402` (`parse_residuals_from_transcript_text`) | invalid row/token skip; `latest.update(row_values)`가 current partial row에 없는 equation을 older row 값으로 보존 | **SILENT-STALE** — mixed-iteration residual dict가 current snapshot처럼 보임. **CLEAR**: one complete row only accept하고 iteration+exact equation set를 함께 반환. **279 전 필수** |
| `src/ro/solver_common.py:700` | malformed report-file row skipped; earlier valid series remains | **SILENT-STALE** — trailing current failure가 earlier stable QoI series로 판단될 수 있음. **CLEAR**: recognized data section의 malformed trailing row를 invalid-current status로 반환 |
| `src/ro/solver_common.py:721,769` | convergence-line iteration becomes `None` 또는 invalid residual row skip; earlier iteration survives | **SILENT-STALE** — stop reason/iteration provenance가 incomplete 또는 old가 될 수 있음. **CLEAR**: marker와 iteration을 atomic parse하고 malformed current tail을 reject |

T2 closure: campaign success를 직접 위협하는 pre-sweep fixes는 solver current-attempt transcript isolation, inlet TUI failure hard-fail/readback, non-None finalized `stop_reason`, full manifest/tree-scan failure propagation, report/driver aggregate nonzero, complete residual equation rows, 그리고 live midplane measurement failure hard-fail이다. Figure-only metadata/style fallbacks는 campaign 수치 생성과 분리할 수 있다.

### T3 — arithmetic identities present but unchecked

#### Batch 1 — run/batch config, registry, base manifest (4 files)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T3-01 | T3 | `configs/run_config.py:414-451,557-563` | numeric validators가 type/sign만 보고 `math.isfinite`를 검사하지 않는다; `nan`은 positive/nonnegative checks를 통과하고 `inf`도 통과한다. | layout, mesh, pressure, tolerance에 non-finite가 들어가 downstream arithmetic/Fluent까지 흐른다. | **CLEAR** — 모든 numeric helper에서 finite를 먼저 require. **279 전 필수**. |
| T3-02 | T3 | `configs/run_config.py:576-639`; `src/ro/campaign_geometry.py:277-352,393-430,466-480` | config의 `family`, `geo_id`, geometry/layout knobs가 registry record와 동일한지 검증하지 않는다. | wrong family, `n_active_cells`, `cell_length_x_m`, `periodic_shift_y`, diameter/bridge가 individually valid이면 통과한다; 과거 divergence class를 재발시킨다. | **CLEAR** — `validate_for_meshing()`에서 registry-derived expected record와 config knobs를 exact/tolerance compare. **279 전 필수**. |
| T3-03 | T3 | `configs/run_config.py:641-677`; `src/ro/manifest.py:319-326` | config는 `mesh_id`의 max/min/cpg/bl/peel token을 대체로 확인하지만 optional `_fNNN`을 `bl_height_factor`와 연결하지 않고, mesh manifest validator는 peel token만 확인한다. | 잘못된 mesh setting metadata가 canonical-looking `mesh_id`로 통과한다. | **CLEAR** — one parser로 `mesh_id` 모든 token을 payload fields와 비교하고 `_fNNN == round(bl_height_factor*1000)` require. **279 전 필수**. |
| T3-04 | T3 | `configs/run_config.py:752-773` | `ramp_full_iteration + post_ramp_buffer_iterations <= max_iterations` 및 `qoi_initial_values_to_ignore + qoi_previous_values_to_consider <= max_iterations`를 검사하지 않는다. | configured convergence window가 한 번도 완성되지 않거나 ramp 이후 solve budget이 없어도 config가 valid다. | **CLEAR** — 두 inequalities를 `validate_for_solver()`에 추가. **279 전 필수**. |
| T3-05 | T3 | `configs/run_config.py:793-802` | legacy `domain_length_m - 2*buffer_length_m > 0`만 검사하고 resolved asymmetric layout의 `buffer_length_in_m + n_active_cells*cell_length_x_m + buffer_length_out_m`와 비교하지 않는다. | 5–27 active-cell layout과 solve-time pressure report span이 어긋나도 통과한다. | **CLEAR** — legacy pair를 제거하고 resolved layout extent identity로 대체. **279 전 필수**. |
| T3-06 | T3 | `src/ro/manifest.py:423-460` | run manifest는 `p_gauge_pa`, `u_target_ms`, `u_mean_ms`, `solver_settings`를 finite type만 확인한다; positivity, `max_iterations` integer, `residual_target > 0`, final artifact에서 `stop_reason != RUNNING`을 묶지 않는다. | negative/zero operating values, fractional/negative iteration budget, unfinished manifest가 individually valid다. | **CLEAR** — semantic sign/integer checks와 finalization-specific validator 추가. **279 전 필수**. |
| T3-07 | T3 | `src/ro/manifest.py:423-482`; `scripts/solver_code_260616.py:117-134`; `src/ro/manifest_validation.py:414-431,638-646` | `run_id`의 `u..._p...` tokens와 `u_target_ms`/`p_gauge_pa`, parabolic `u_mean_ms * inlet_profile_G == u_target_ms`를 검증하지 않는다. `u_mean_source_mesh_id == mesh_id`는 이미 검사하므로 누락이 아니다. | mislabeled operating point와 잘못된 legacy coefficient가 valid manifest로 남는다. | **CLEAR** — standard run-id parser와 parabolic coefficient equality를 cross-field validator에 추가; tolerance를 명시. **279 전 필수**. |
| T3-08 | T3 | `configs/batch_config.py:297-373,399-450,488-498` | lists에는 geometry/run uniqueness, expected `31` geo set, 각 geo의 `3x3`, total `279` identity를 assert하는 code가 없다; current list는 exploratory 22 meshes/5 runs다. | production list를 hand-edit할 때 omission/duplicate가 valid config로 실행된다. | **CLEAR** — registry와 velocity/pressure Cartesian product에서 generate하고 exact set/cardinality/unique four-ID assert. **279 전 필수**. |

#### Batch 2 — geometry/layout/mesh validators (4 files)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T3-09 | T3 | `src/ro/manifest_validation.py:554-575,638-670` | mesh의 `membrane_blocked_area_frac`는 `[0,1)`만 검사하고 run geometry validator는 이 field를 전혀 검사하지 않는다. campaign identity는 정확히 `0.0`이다. | 과거 Pillar `0.08 / 0.06 / 0.05`가 valid가 되어 LMH downstream으로 흐르며, mesh/run 값도 달라질 수 있다. | **CLEAR** — mesh와 run 모두 exact `0.0` require하고 cross-manifest equality도 검사. **279 전 필수**. |
| T3-10 | T3 | `src/ro/domain_layout.py:501-532`; `src/ro/mesh_common.py:483-520`; `scripts/meshing_code_260616.py:45-89` | `validate_layout_against_x_extent()`는 있으나 live manifest payload가 parsed `domain_extent_x_m`를 버리고 이 validator를 호출하지 않는다. 정확한 identity는 `buffer_length_in_m + n_active_cells*cell_length_x_m + buffer_length_out_m == domain_extent_x_m`이다. | individually valid인 sinusoidal `cell_length_x_m` 오류와 `n_active_cells` 오류가 모두 통과한다; 이 identity였으면 두 과거 bug를 잡았다. | **CLEAR** — measured extent를 manifest/schema에 보존하고 write 전 `validate_layout_against_x_extent()`를 hard gate로 호출. **279 전 필수**. |
| T3-11 | T3 | `src/ro/manifest_validation.py:183-226` | `periodic_shift_y_source="derived_from_angle"`에서 `sin(theta)>0`만 확인하고 `periodic_shift_y_m`가 angle/geometry로부터 실제 유도된 값인지 비교하지 않는다. | 임의의 positive shift가 valid이며 과거 `periodic_shift_y_m` divergence를 놓친다. | **CLEAR** — registry expected value와 compare하거나 Diamond에 `cell_length_x_m*cos(theta) == periodic_shift_y_m*sin(theta)`를 tolerance로 검사. **279 전 필수**. |
| T3-12 | T3 | `src/ro/manifest_validation.py:576-583` | `membrane_contact_width_m`는 nonnegative만 확인하고 geometry inputs와의 `2*sqrt(d*t-t^2)` identity를 검사하지 않는다. | 잘못된 contact-width metadata가 valid다; 확인된 과거 bug는 없다. | **CLEAR** — 필요한 diameter/overlap fields가 있는 family에서 registry formula를 재계산. scoring에 미사용이면 sweep 후로 유예 가능. |
| T3-13 | T3 | `src/ro/manifest_validation.py:434-552,608-624` | ML lists는 길이/finite만 검사한다. `filament_d_m == layer_diameters_m[1]`, `Sigma_d_nominal_m == sum(layer_diameters_m)`, `layer_axis_z_m`와 `joint_sphere_z_m`의 diameter-derived positions, `joint_sphere_r_min_m == min(layer_diameters_m)/2`, `bridge_radius_m == joint_sphere_R_m`, expected `layer_angles_deg`가 unchecked다. | internally inconsistent ML geometry metadata가 valid하며, 과거 `layer_axis_z_m` source divergence를 막지 못한다. CAD와 실제 일치 여부는 `C:/ro_data`/live geometry 없이는 **UNKNOWN**이다. | **CLEAR** — registry helper와 동일한 pure computation으로 내부 identities를 validate; CAD cross-check는 별도 workstation gate. **279 전 필수**. |
| T3-14 | T3 | `src/ro/manifest_validation.py:356-411` | sinusoidal `curvature_margin`은 band/acknowledgement만 검사하고 `wavelength_m`, `amplitude_m`, filament radius로 재계산하지 않는다. | stale/mistyped finite margin이 valid다; 확인된 과거 bug는 없다. | **CLEAR** — manifest inputs로 registry formula를 재계산해 equality 검사. threshold의 scientific adequacy는 **NEEDS-ASTRA**이나 이 arithmetic check는 명확하다. |
| T3-15 | T3 | `src/ro/mesh_common.py:51-74,483-520,671-714`; `scripts/meshing_code_260616.py:73-88` | parser data의 `bounding_box_volume_m3 == dx*dy*dz`, `porosity == total_fluid_volume_m3/bounding_box_volume_m3`, `skewed_face_fraction == skewed_faces_over_080/surface_face_count`, `min_cell_volume_m3 <= max_cell_volume_m3`를 ledger/manifest가 재검사하지 않으며 manifest는 대부분 fields를 버린다. quality gate는 non-finite도 reject하지 않아 `nan` 비교가 PASS로 끝날 수 있다. | corrupt/partial metrics가 plausible current record 또는 PASS가 된다; 확인된 과거 bug는 없다. | **CLEAR** — complete measured tuple를 ledger/manifest에 보존하고 finite+arithmetic validator를 quality gate 앞에 둔다. quality finite check는 **279 전 필수**; diagnostic volume identities는 유예 가능. |
| T3-16 | T3 | `src/ro/mesh_common.py:641-668` | `boundary_layer_labels`, `bl_height`, `vol_hex_max`는 missing일 때만 derived되며 explicit 값이 `active+buffer(+spacer)`, `m_min*bl_height_factor`, `m_max*vol_hex_max_factor`와 같은지 검사하지 않는다. | duplicate explicit field가 stale여도 worker가 그 값을 우선 사용한다. | **CLEAR** — canonical derivation을 항상 계산해 explicit override와 equality/set-equality 검사. **279 전 필수**. |
| T3-17 | T3 | `src/ro/domain_layout.py:449-467` | solver replace-log가 있으면 `mesh_id` equality를 검사하지만 log가 없으면 정상 return한다. completed run이라는 state와 replace-log/manifest provenance의 존재 identity가 없다. | mesh replacement provenance가 missing이어도 run validation이 성공할 수 있다. | **CLEAR** — completed run gate에서 current-attempt replace log 존재와 quoted mesh path/hash를 require. **279 전 필수**. |

#### Batch 3 — report/metric arithmetic (4 files)

| id | task | file:line | 무엇인가 | 무엇이 깨지는가 | triage |
|---|---|---|---|---|---|
| T3-18 | T3 | `src/ro/fluent_report_helpers.py:490-500,531-538,561-588` | required integer layout keys를 type-check 전에 `int()`로 변환한다. | `7.9` 같은 invalid count가 `7`로 잘려 valid layout/window가 된다. | **CLEAR** — cast 전에 bool 제외 exact `int` type을 require. **279 전 필수**. |
| T3-19 | T3 | `src/ro/fluent_report_helpers.py:746-783,937-979`; `scripts/pyfluent_report_extract.py:2431-2435` | load-bearing gates는 column/report가 nonblank인지만 검사하고 numeric type/finite/sign/range 및 서로의 arithmetic consistency를 검사하지 않는다. | `nan`, `inf`, wrong-key finite value도 successful extract가 된다. | **CLEAR** — schema별 typed finite validator를 먼저 두고 아래 identities를 함께 gate. **279 전 필수**. |
| T3-20 | T3 | `scripts/pyfluent_report_extract.py:1275-1316,1759-1767,2055-2076` | 3-key flux payload를 저장하지만 `m_*_with_sources == m_* + m_*_mass_source`를 inlet/outlet 각각 검사하지 않는다. | payload parser가 다른 key의 finite number를 고르는 과거 bug가 재발해도 모든 gate가 PASS한다. | **CLEAR** — parse 직후 두 decomposition equalities를 tolerance로 hard gate. **279 전 필수**. |
| T3-21 | T3 | `scripts/pyfluent_report_extract.py:1021-1051,1767-1796,2031-2040,2082-2091`; `src/ro/lmh_metrics.py:36-53,65-87` | 독립 경로가 함께 저장되지만 `lmh_mass_balance == abs(lmh_mass_balance_signed)`, `lmh_mass_balance_signed == lmh_mass_balance_signed_python`, `lmh_mass_balance`와 `lmh_udm_avg`의 허용차 identity를 gate하지 않는다. | stale Fluent expression/report state 또는 wrong input이 finite output으로 남는다; 현재는 difference만 기록한다. | **CLEAR** — signed/unsigned/Python equality는 tight tolerance hard gate; UDM-vs-balance 허용차는 **NEEDS-ASTRA**가 campaign tolerance를 결정. **279 전 필수**. |
| T3-22 | T3 | `scripts/pyfluent_report_extract.py:1075-1080,1772-1775,1860-1885,1890-1967,2093-2104`; `src/ro/fluent_report_helpers.py:2454-2487,2545-2566` | `pressure_drop == p_in_avg-p_out_avg`, `pressure_drop_spacer == p_spacer_in_avg-p_spacer_out_avg`, per-cell drops의 telescoping sum, `*_per_m * length == drop`, boundary count `== layout.n_total+1`를 cross-check하지 않는다. | wrong report mapping 또는 missing/misaligned boundary가 finite dP CSV를 만든다. | **CLEAR** — output write 전 pressure identity validator를 추가. **279 전 필수**. |
| T3-23 | T3 | `src/ro/cp_metrics.py:304-338,349-419`; `src/ro/fluent_report_helpers.py:1346-1446,1840-1907` | CP/window helpers가 positive만 검사하고 inputs/results의 finite, exact unique evaluation-cell set, `max >= avg`, aggregate recomputation equality를 output contract로 검사하지 않는다. `nan`은 `<=`/threshold 비교를 우회할 수 있다. | partial/duplicate/non-finite cell data가 finite-looking schema 또는 `nan` successful output으로 흐른다. | **CLEAR** — expected window exact-set + finite + ordering + recomputed aggregate validator 추가. 정의의 scientific validity는 이 pass 범위 밖. **279 전 필수**. |
| T3-24 | T3 | `src/ro/fluent_report_helpers.py:291-342` | midplane cross-check는 `left/right > 0`만 검사한다; `c_b`, boundaries 또는 `rel_tol`이 `nan`이면 `rel_err > rel_tol`가 false여서 PASS한다. | CP bulk-plane placement gate가 non-finite data에서 무력화된다. | **CLEAR** — all operands와 tolerance를 finite/nonnegative로 require. **279 전 필수**. |
| T3-25 | T3 | `src/ro/fluent_report_helpers.py:1910-1941`; `scripts/pyfluent_report_extract.py:1909-1948` | mass-fraction↔molar conversion은 density/MW positive만 검사하고 input finite 및 mass fraction `[0,1]`을 검사하지 않는다; 두 forms를 함께 저장해도 round-trip equality가 없다. | invalid concentration이 canonical inputs로 전파될 수 있다. | **CLEAR** — finite/range check와 round-trip validator 추가. **279 전 필수**. |
| T3-26 | T3 | `scripts/pyfluent_report_extract.py:1765-1766,2079-2080`; `src/ro/fluent_report_helpers.py:656-660` | wall `area_mem`과 UDM volume-sum `pp_udm_area_sum`이 모두 load-bearing이나 서로 비교하지 않는다. | area accumulator가 stale/wrong zone여도 nonblank gate를 통과한다. | **NEEDS-ASTRA** — Fluent discretisation상 두 값의 expected equality와 허용차를 정한 뒤 cross-check; 결정 전에는 discrepancy를 hard gate로 쓰지 말 것. |
