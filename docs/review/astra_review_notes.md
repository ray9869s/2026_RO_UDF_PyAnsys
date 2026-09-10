# Astra repository review — 중단 복구용 체크포인트

## Progress — 이 파일이 검토 상태의 source of truth

- 체크포인트 날짜: 2026-09-10. Section A 추가 감사 진행 중. 아래 A 확장 기록이 기존 A 잠정 메모보다 우선한다.
- 대상: `ray9869s/2026_RO_UDF_PyAnsys`, WSL `/home/jongwookim/code/pyfluent`.
- 검토 기준 commit: `4fc26b776a3dd29a9cce44817ef4ebe714b42e72`. 당시 WSL `main`은 clean 상태였고 GitHub `main`과 동일했다.
- A correctness and silent failure: **PARTIAL**. `manifest.py`, `domain_layout.py`, `campaign_geometry.py` 후반, 주요 config, `cp_metrics.py` 전반, solver 일부를 확인했다. 중단 지점: 전체 `except` 분류, 중복 source-of-truth 전수 목록, layout magic number 전수 목록, validator live call graph 및 cross-field 검증은 미완료.
- B scientific validity: **PARTIAL**. docs의 CP 정의와 `cp_metrics.py` 수식, solver의 물성 확인 경로를 확인했다. 중단 지점: production UDF → report helpers → extractor → 최종 CSV 전체 추적 미완료. Bae/Liang/Fluent 공식 자료 검증, empty-channel dP 원인, Diamond 시간 안정성 및 실제 mesh 해석은 미완료.
- C robustness: **PARTIAL**. solver의 launch, manifest 기록, 종료 및 artifact 확인 순서를 확인했다. 중단 지점: meshing/sweep/rerun/extract 전 경로의 retry·skip·recovery 감사, report API 비용 개선의 실제 호출 검증 미완료.
- D repository quality: **PARTIAL**. 문서 대부분 및 test baseline을 확인했다. 중단 지점: dead code/call graph, 테스트가 놓치는 실패 모드 전수 대조, legacy naming 전수 검색, docs-code 대조 미완료.
- **COMPLETE인 섹션: 없음. NOT STARTED인 섹션: 없음.** 네 섹션 모두 일부만 검토했다. 아래 개별 항목도 확정된 관찰과 검증 대기 가설을 구분한다.
- 다음 턴부터 한 섹션씩 작업한다. 해당 섹션의 결과를 이 파일에 먼저 추가하고 progress block을 갱신한 다음 채팅으로 보고한다. COMPLETE로 표시된 섹션은 다시 검토하지 않는다.
- 사용자 지시: source/config/tests는 변경하지 않는다. 이 리뷰 노트만 작성 허용. 이 턴은 체크포인트 작성 후 종료한다.

## 증거 수준과 제한

- **CODE-CONFIRMED**: 읽은 코드에서 확인한 동작. 실제 campaign 결과가 이미 오염되었다는 뜻은 아니다.
- **CANDIDATE / UNKNOWN**: 코드 관찰은 있으나 호출 연결, Fluent 의미, 데이터 또는 실행 검증이 더 필요한 잠정 finding.
- `C:/ro_data`, 공용 Windows workstation, live Fluent, 실제 solver run에는 접근하지 않았다. 수치 결과와 재현 측정은 사용자가 제공한 것으로 독립 검증하지 않았다.
- 아래 line은 검토 당시 기록이다. 파일 전체를 아직 확인하지 않은 항목은 범위 또는 재확인 필요라고 명시했다. 정밀 line을 새로 찾는 작업은 이번 체크포인트에서 수행하지 않았다.
- review 중 코드 변경 없음. WSL pure-Python test만 실행했고 Fluent를 실행하지 않았다.
- Windows 대화 workspace `C:/Users/User/Documents/ChatGPT/pyfluent`는 실제 WSL code checkout과 다른 빈 초기화 repository였다. 검토 대상 코드는 WSL checkout이다.

## Campaign 전에 우선 해결하거나 검증해야 할 항목

### B-01 — local bulk c_b가 실제 mixing-cup concentration인지 미확정

- 위치: `docs/POSTPROCESSING_MAP.md`, `docs/metrics_conventions.md`의 CP/local bulk 설명; `src/ro/fluent_report_helpers.py`의 midplane report 경로는 상세 line 및 구현 추적 미완료.
- 상태: **CANDIDATE / UNKNOWN**.
- 관찰: 문서는 unit-cell x 범위로 자른 `z=0` midplane에서 `surface-massavg`로 c_b를 얻는다고 설명한다.
- 무엇이 깨지는가: Fluent의 해당 연산이 면 법선 방향 mass flux를 가중치로 사용한다면, streamwise u가 아니라 z 방향 흐름을 가중하게 된다. 또한 midplane 평균은 y-z 단면 전체 mixing-cup 평균과 동일하지 않다. 이 경우 canonical CP의 denominator 자체가 의도한 local bulk가 아니며 geometry ranking에도 영향을 줄 수 있다.
- 근거/한계: 문서상 평면 방향과 weighting 조합에서 제기한 문제다. Fluent 공식 정의, 실제 생성된 surface, 실제 호출을 아직 검증하지 않았다. 확정된 오류로 취급하면 안 된다.
- 최소 해결: 먼저 surface 방향과 Fluent weighting을 확인한다. 의도와 다르면 streamwise transverse section의 salt mass flow / fluid mass flow 등으로 c_b를 정의하고, membrane evaluation window에 어떻게 대응시키는지 명시한다. 기존 방식과 차이는 실제 데이터로 정량화해야 한다.

### B-02 / A-01 — scalar-k CP spread guard 수식이 일반적인 상한을 보장하지 않음

- 위치: `src/ro/cp_metrics.py:280-301`, `:304-338`; `configs/post_config.py:145`.
- 상태: **CODE-CONFIRMED** 수식 관찰. 실제 campaign 영향은 **UNKNOWN**.
- 관찰: guard는 `abs(c0-cb)*(cp_max-cp_min)/(cb-cp_min)^2`를 사용한다. canonical 변환은 `k=(c0-cp)/(cb-cp)`다. `cp_max < cb`인 경우에도 k의 양 끝 차이는 `abs(c0-cb)*(cp_max-cp_min)/((cb-cp_max)*(cb-cp_min))`다. 기록된 guard는 이보다 작아질 수 있다.
- 무엇이 깨지는가: scalar k 근사가 허용 오차 이내라는 판정을 충분한 수학적 근거 없이 통과할 수 있다. `cp_max >= cb`, reversed bounds, NaN에 대한 검증도 해당 helper에서 충분하지 않다. bounds가 없으면 `(k, 0)`이 반환된다 (`:325-326`).
- 근거: 위 코드 수식 및 비교문. 예를 들어 `cb=100`, `c0=100.1`, `cp_min=0`, `cp_max=99`이면 현재 식은 약 `0.00099`, 양 끝 k 차이는 약 `0.099`다. 이는 실제 물리값 주장 아닌 수식 반례이며 실행 test는 아직 추가하지 않았다.
- 추가 위험: quantile bounds를 사용하는 경우 전체 평균/최댓값에 대한 엄밀한 bound와 같지 않다. 현재 `compute_cp_spread=False`가 기본값이므로 certification 자체가 수행되지 않는다. docs는 `not_evaluated`를 구별한다고 설명하지만 최종 CSV gate 연결은 미확인.
- 최소 해결: finite/order/denominator 검증 후 올바른 bound를 사용하고, missing bounds는 0 error가 아니라 미검증 상태로 반환한다. quantile 기반 근사와 전체 bound를 구별한다. campaign gate에서 미검증 값을 허용할지 명시적으로 결정한다.

### B-03 — CP variants는 명칭만 보고 통합하면 안 됨; 요청문의 L1 설명과 코드가 다름

- 위치: `src/ro/cp_metrics.py:239-255`, `:304-346`; `docs/metrics_conventions.md`, `docs/POSTPROCESSING_MAP.md` CP 정의 절. CSV 및 production UDF line 추적 미완료.
- 상태: **CODE-CONFIRMED** helper 정의; 전체 downstream 일관성은 **UNKNOWN**.
- 관찰: 확인한 정의는 canonical `(cm-cp)/(cb-cp)`, L1 `cm/cb`, L2 `(cm-cp)/(c0-cp)`다. 요청문은 L1을 c_0 denominator라고 표현했지만 현재 helper의 L1은 c_b denominator다. L2는 docs상 UDM9와 연결된다.
- 무엇이 깨지는가: column/plot 선택이나 label이 어긋나면 geometry 간 신호와 bulk correction을 혼동한다. 아직 실제 CSV에서 혼용된 사례를 확정하지 않았다.
- 최소 해결: CSV별 formula/source/aggregation/window mapping을 완성하고 tests로 고정한다. 요청문의 명칭을 따라 식을 임의로 바꾸지 않는다.
- 추가 미확인: docs상 UDF face 결과를 cell UDM으로 area-weight한 뒤 surface interpolation을 거친다. 따라서 원래 per-face CP average 또는 max와 완전히 같은지 검증이 필요하다. `F_UDMI` 직접 접근이 불가능하다는 설명만으로 동등성을 주장할 수 없다.

### B-04 — 0.6% signal에 대한 numerical resolution은 아직 입증되지 않음

- 위치: `docs/MESH_LANDSCAPE.md`, `docs/metrics_conventions.md`의 grid/CP 논의; 정확한 line 재확인 필요.
- 상태: **UNKNOWN — 실제 grid-study outputs와 campaign 데이터 미접근**.
- 관찰: 사용자 제공 `8%`는 CP 전체가 아니라 CP excess의 grid dependence다. `CP≈1.0785`를 예로 쓰면 `0.08*0.0785=0.00628`, 전체 CP의 약 `0.582%`로, `0.6%` between-geometry signal과 같은 크기다. 서로 다른 기준의 퍼센트를 직접 비교하면 안 된다.
- 무엇이 깨지는가: 현재 자료만으로 Diamond CP ranking의 유의성을 보장할 수 없다. 모든 geometry의 오차가 공통으로 상쇄되는지도 unknown이다.
- 최소 해결: 최종 canonical CP와 동일한 window/aggregation으로 matched-pair mesh refinement를 수행하고, pairwise differences/ranking의 안정성을 확인한다. c_m만의 grid convergence는 최종 CP ratio convergence의 대체물이 아니다. 충분히 분리되지 않으면 CP를 ranking 지표가 아니라 indistinguishable 결과로 보고할 연구 framing이 필요하다.

### A-02 — geometry metadata에 config와 registry라는 두 source가 계속 존재

- 위치: `src/ro/campaign_geometry.py:483-521`, `:524-559`; `scripts/solver_code_260616.py:51-105`.
- 상태: **CODE-CONFIRMED** precedence. 특정 산출물의 오염은 **UNKNOWN**.
- 관찰/누가 이기는가: mesh merge는 spacing/angle/filament/bridge/overlap/n_active_cells/cell_length/periodic 등 선택된 config key에 config 우선권을 준다. 나머지 registry geometry fields는 registry가 payload를 덮어쓴다. run merge는 `_GEOMETRY_FIELDS`를 registry에서 가져온다. solver manifest builder는 mesh hash를 읽지만 모든 실제 mesh geometry metadata를 run에 복사하지 않는다.
- 무엇이 깨지는가: custom mesh config 또는 측정된 mesh metadata와 run metadata가 다를 수 있다. 예: mesh의 measured porosity는 run registry의 `None`으로 대체될 여지가 있다. bridge/layer/joint 값도 독립적으로 달라질 수 있다. 현재 blocked fraction이 0으로 일치한다고 구조적 문제가 사라지는 것은 아니다.
- 구체적 pair: `configs/batch_config.py:132-155`의 공통 angle `45`를 Pillar case 생성 `:312-324`가 명시적으로 바꾸지 않는 반면, Pillar registry `campaign_geometry.py:355-390`는 angle `0`이다. 실제 meshing 영향은 호출 추적 추가 필요지만 metadata precedence 불일치는 보인다.
- 최소 해결: mesh generation 시 하나의 resolved geometry object를 만들고 config/registry 충돌은 fail-fast한다. run geometry는 검증된 mesh manifest에서 상속하고 registry 재계산값과 충돌하면 중단한다. 모든 중복 pair의 전수 목록은 아직 미완료다.

### A-03 — schema version과 required fields 변경을 통한 migration 계약이 약함

- 위치: `src/ro/manifest.py:60-81`, `:83-137`, `:293-420`, `:753-788`.
- 상태: **CODE-CONFIRMED** 구조. 이번에 새로 발견한 별개의 과거 장애라고 주장하지 않음.
- 관찰: schema version은 `2`로 유지되고 `_GEOMETRY_FIELDS`에는 required `membrane_blocked_area_frac_geometric`가 포함된다. read path는 바로 validation한다.
- 무엇이 깨지는가: 같은 version의 기존 manifests가 required field 추가만으로 read 불가능해질 수 있다. 사용자가 설명한 34/44 장애와 같은 구조다. 현재 data root 전체 migration 완료 여부는 **UNKNOWN**.
- 최소 해결: 명시적 schema version migration 또는 read-upgrade 정책과 전체 persisted-manifest compatibility fixture를 둔다. migration 도중 실패/부분 완료도 test한다.

### A-04 — layout arithmetic check가 격리되어 있고, 실제 artifact geometry와의 연결 미확인

- 위치: `src/ro/domain_layout.py:145-177`, `:315-353`, `:501-532`; `src/ro/manifest.py:293-420`; `scripts/solver_code_260616.py:2744-2750`.
- 상태: **CODE-CONFIRMED** 계산 및 helper 존재; live caller 유무 전수 검증은 **UNKNOWN**.
- 관찰: 실제 total length 정의는 `buffer_length_in + n_active_cells*cell_length_x_m + buffer_length_out`다. 사용자 제안의 `cell_length_x_m*(n_buffer_in+n_active_cells+n_buffer_out)`는 buffer lengths가 각 count*pitch일 때만 동일하다. buffer cell들은 각 buffer length를 나누어 만든다.
- 무엇이 깨지는가: field별 range 검증만으로 domain extent/count/periodic pitch 불일치를 잡지 못한다. `validate_layout_against_x_extent`는 존재하지만 result를 반환하며 자체적으로 raise하지 않는다. solver의 `mesh/check` 호출만으로 manifest arithmetic 일치를 보장할 수 없다.
- 추가 관찰: manifest는 buffer counts에 `0`을 허용하는 반면 DomainLayout은 `>=1`을 요구해, manifest-valid가 runtime-layout-valid를 보장하지 않는다.
- 최소 해결: 의도한 buffer 정책을 먼저 확정하고 measured x extent와 위 실제 total length를 비교하는 gate를 live path에 연결한다. helper가 테스트에서만 호출되는지 다음 A/D pass에서 확인해야 한다.

### A-05 / C-01 — byte hash/provenance 검증 없이 metadata/path identity에 의존

- 위치: `src/ro/manifest.py:293-420`, `:673-715`, `:753-788`; `src/ro/domain_layout.py:341-353`, `:422-467`; `scripts/solver_code_260616.py:2626-2640`, `:2648` 이후.
- 상태: **CODE-CONFIRMED** 확인한 경로. 상위 gate 전체는 미확인.
- 관찰: schema는 hash 형식을 검사하지만 실제 mesh bytes와 대조하지 않는다. read_run/read_mesh와 layout_from_run도 cross-manifest geometry/hash 일치를 확인하지 않는다. solver preflight는 file 존재를 확인하지만 기록된 hash와의 대조는 확인하지 못했다. restart pair는 경로 중복을 검사하지만 source provenance 일치는 미확인이다.
- 무엇이 깨지는가: 같은 mesh_id 아래 mesh 파일이 교체되거나 잘못된 restart가 선택되어도 metadata가 정상처럼 보일 수 있다. overwrite guard도 file/manifest/hash가 없으면 허용하는 경로가 있다.
- 추가 관찰: `mesh_case_name_from_solver_replace_log`는 로그를 정렬하고 처음 matching log에서 반환한다. 문서상 later replacement 우선 의도와 다를 수 있다. 로그가 없으면 assert는 반환한다. byte identity 검증의 대안은 아니다.
- 최소 해결: 실제 mesh bytes hash와 run.mesh_hash를 solver 시작 및 재추출 preflight에서 확인한다. restart에는 source run identity/physics/hash를 검증한다. 로그는 가장 최신 유효 실행을 명시적으로 선택하되 manifest provenance를 주 근거로 삼는다.

### A-06 / B-05 — 설정된 물성과 실제 solver/UDF 물성의 일치가 충분히 gate되지 않음

- 위치: `configs/post_config.py:72-76`; `scripts/solver_code_260616.py:365-409`, `:551-599`, `:2978-3024`.
- 상태: **CODE-CONFIRMED** 관찰. UDF 전체와 template 실제 state는 **UNKNOWN**.
- 관찰: post config는 rho/mu/c0/MW/B 상수를 갖는다. UDF patch helper는 `SALT_YI`, `U_TARGET`를 바꾼다. solver는 viscous/species/energy 및 density/viscosity를 주로 출력하고 mass diffusivity에는 명시적 assert를 한다. mixture-template이 없으면 첫 mixture를 고르는 fallback이 있다.
- 무엇이 깨지는가: template의 viscosity/density/model이 의도와 달라도 출력만 남고, postprocessing analytic reference 또는 flux conversion은 별도 상수를 쓸 수 있다. empty-channel dP discrepancy 원인이라고 확정할 수는 없다.
- 최소 해결: 실제 assigned material/model의 물성값을 finite/tolerance gate로 확인하고 manifest에 기록한다. UDF constants, config, actual Fluent state의 계약을 하나로 검증한다. `u_mean_ms`를 physical bulk velocity로 바꾸는 수정은 하지 않는다.

### C-02 — solver crash 후 manifest/artifact 상태가 일관되게 복구 가능하지 않음

- 위치: `scripts/solver_code_260616.py:2695-2716`, `:2765-2786`, `:3664-3749`, `:3753-3801`.
- 상태: **CODE-CONFIRMED** 순서 및 exception 동작. campaign orchestrator 보완 여부는 **UNKNOWN**.
- 관찰: run directory 생성 후 launch/switch를 수행하고 그 다음 RUNNING manifest를 쓴다. 따라서 launch 또는 mode switch 실패는 manifest 없는 run leaf를 남길 수 있다. 종료 시 manifest finalization은 final case/data write보다 먼저 실행된다.
- 무엇이 깨지는가: startup crash로 orphan leaf가 생긴다. final write 실패 시 metadata가 완료/수렴 상태를 가리키지만 artifact가 없거나 이전 실행 것이 남을 수 있다. 마지막 확인은 file 존재/비어 있지 않음을 보며 새 artifact라는 증거까지 보장하지 않는다.
- 최소 해결: 외부 orchestrator에서 launch 전에 attempt identity와 상태를 기록한다. final outputs는 임시 이름에 쓴 뒤 검증하고 완료 상태를 commit한다. 이전 artifact와 새 attempt를 분리하고 freshness/hash를 검증한다.
- 추가 미확인: 확인한 solver path에서 주기적 checkpoint/autosave를 발견하지 못했지만 파일 전체 검색을 끝내지 않았다. 따라서 38-minute crash 때 무조건 전부 손실된다고 아직 단정하지 않는다. checkpoint와 resume 정책은 다음 C pass에서 확인한다.

### A-07 / C-03 — stop reason 결정 실패는 warning 후 정상 종료로 흐를 여지

- 위치: `scripts/solver_code_260616.py:3669-3689`, `:3733-3749`.
- 상태: **CODE-CONFIRMED** exception control flow; downstream inventory gate는 **UNKNOWN**.
- 관찰: stop reason 처리의 exception은 warning 처리된다. `solver_stop_reason`이 None이면 finalization을 건너뛰어도 case/data write 경로는 진행한다.
- 무엇이 깨지는가: output files가 있으면 process success와 RUNNING/미완성 convergence metadata가 공존할 수 있다. 다음 stage가 반드시 이를 거부하는지는 아직 검증하지 않았다.
- 최소 해결: stop reason 판정 실패는 명시적 failed/unknown 상태를 기록하고 nonzero 종료 또는 필수 gate failure로 처리한다.
- 대조: `calculate` exception `:3664`와 outer exception `:3753`은 re-raise하므로 이 둘을 silent success로 분류하면 안 된다. cleanup exceptions `:3766-3784`는 warning만 남기며, 결과 신뢰성보다는 process/resource recovery 관점에서 별도 분류가 필요하다.

### A-08 — helper의 nonfinite/invalid bracket 성공값 전파 위험

- 위치: `src/ro/cp_metrics.py:78-173`, 특히 `:118-125`; `:176-236`, 특히 `:226-227`; `:239-255`; `configs/post_config.py:34-59`.
- 상태: **CODE-CONFIRMED** helper 동작. 모든 호출자의 gate 여부는 **UNKNOWN**.
- 관찰: quantile bisection에서 upper bracket이 target에 도달하지 못해도 upper bound와 iteration count를 반환한다. minimum support search에서도 확장 한도 후 upper bound와 boolean을 반환한다. caller가 그 의미를 확인하는지 미검증이다. film cp helper에는 finite check가 없다. post override는 key allowlist만 확인하며 types/finite 검사까지 하지는 않는다.
- 무엇이 깨지는가: probe 실패/범위 부족/NaN이 그럴듯한 scalar로 변해 CSV 또는 guard를 통과할 수 있다.
- 최소 해결: bracket validity/convergence/support 여부를 명시적 result로 반환하고 필수 caller gate를 둔다. physical inputs와 report outputs에 finite/domain checks를 적용한다.

### A-09 — 일부 filesystem/Fluent introspection errors가 빈 목록으로 바뀜

- 위치: `src/ro/manifest.py:821-825`, `:855-885`; `scripts/solver_code_260616.py:601-700`.
- 상태: **CODE-CONFIRMED** catches. downstream 영향은 **UNKNOWN**.
- 관찰: manifest iteration helper는 OSError를 빈 child list로 바꾼다. iter_run에는 invalid manifest를 log/skip하는 옵션이 있다. Fluent name introspection은 여러 API fallback exceptions를 무시하고 최종적으로 빈 목록을 반환할 수 있으며 category enumeration도 continue한다.
- 무엇이 깨지는가: 접근 실패와 실제로 비어 있는 tree/zone list가 구분되지 않을 수 있다. run 누락 또는 validation 우회로 이어지는지 caller별 확인이 필요하다.
- 최소 해결: empty/unsupported/error를 구분하고 필수 discovery는 fail-closed한다. 선택적 diagnostics fallback은 status를 output에 남긴다. 모든 `except` 전수 분류는 아직 하지 않았다.

## Scientific questions — 아직 원인을 확정하지 않은 항목

### B-06 — empty-channel dP의 velocity-dependent excess

- 위치: `scripts/solver_code_260616.py:2978-3024`는 물성 관찰 관련 위치; dP report 생성/집계 line은 추적 미완료. 관련 기록은 `docs/MESH_LANDSCAPE.md` 등.
- 상태: **UNKNOWN — actual case/data, mesh, Fluent state, report 정의 필요**.
- 알려진 내용: 사용자 제공 결과는 u=0.2에서 2.6%, u=0.3에서 3.9% analytic plane Poiseuille 초과다. periodic sides, residual convergence, permeation/inertia 약 0.1% 조건을 존중한다.
- 미확정 설명: actual viscosity, pressure fitting/window, inlet discrete shape, convection numerical error 등을 분리해야 한다. velocity에 따른 numerical convection 기여는 가능한 가설이지 확인된 원인이 아니다. G 약 0.36% 또는 작은 nominal-height 차이만으로 해당 크기를 설명한다고 주장하지 않는다.
- 최소 다음 조치: 실제 material/height/flow rate, 동일 report로 얻는 x-profile, discretization sensitivity를 대조한다. solver run 없이 원인을 단정할 수 없다.

### B-07 — Diamond u=0.3 steady result의 비교 가능성

- 위치: docs의 Diamond convergence/grid-study 기록; solver convergence 경로 `scripts/solver_code_260616.py:3664-3749`. 정확한 diagnostic aggregation line 미확인.
- 상태: **UNKNOWN — transient stability와 spatial development 검증 필요**.
- 알려진 내용: 사용자 제공 cells 4/5의 7.8%/15.0% dip, 765 iterations 지속, empty-channel uniformity 0.53%, grid-study spread 1.90%–3.59%를 독립 검증하지 않았다.
- 무엇이 깨지는가: residual convergence는 physical steady-state stability 또는 dP grid convergence를 증명하지 않는다. free-cylinder shedding onset 약 47을 confined spacer의 임계 Re로 그대로 적용하는 것도 타당성 증명이 아니다.
- 최소 다음 조치: perturbation/initialization sensitivity, transient timestep+mesh refinement와 time-averaged metrics, development length 및 evaluation-window sensitivity를 구분해서 시험한다. evidence가 없으면 u=0.3 ranking을 검증 완료라고 주장할 수 없다.

### B-08 — CP near-wall mesh 적정성

- 위치: `docs/MESH_LANDSCAPE.md`, `configs/batch_config.py:132-155`; UDF analytic reconstruction 전체 추적 미완료.
- 상태: **UNKNOWN — representative concentration profiles/refinement와 primary literature 확인 필요**.
- 관찰: 사용자 제공 4 prism layers/첫 cell 약 6.2 um/core 약 7.7% h, Sc 약 600–700는 CP resolution concern을 제기하기 충분하다. `Sc^(-1/3)`는 약 0.11–0.12이지만 이것만으로 필요한 첫 cell 수치를 확정할 수 없다.
- 무엇이 깨지는가: momentum quality와 residual convergence로 concentration layer resolution을 보증할 수 없다. analytic reconstruction 역시 모든 streamwise/lateral concentration error를 제거한다고 가정할 수 없다.
- 최소 다음 조치: wall-normal concentration profile, membrane gradient/flux 및 최종 canonical CP에 대한 targeted refinement. 12 layers의 aspect ratio 252만으로 모든 high-layer mesh가 과학적으로 불가하다고 결론내리지 말고 alignment/orthogonal quality/solver behavior를 함께 평가한다. Liang의 설정 비교는 원문 검증 전 잠정 사항이다.

### B-09 / A-10 — inlet_profile_G constant의 검증 범위

- 위치: `src/ro/campaign_geometry.py` Diamond registry 전반(정밀 line 미기록); `configs/batch_config.py:488-498`; production `udfs/260822_RO_UDF.c` 전체 검토 대기.
- 상태: **CANDIDATE / UNKNOWN**.
- 관찰: empty와 D2450의 G가 3.2e-6로 일치한다는 측정은 존중한다. 다만 Diamond family registry는 width/pitch가 다양하며 두 개 mesh의 일치가 전31개 geometry의 discrete profile integral 일치를 자동으로 입증하지는 않는다. UDF에 고정 geometry/area 상수가 남아 있는지 다음 B/A pass에서 확인해야 한다.
- 최소 해결: campaign constant를 당장 바꾸지 않는다. 전체 inlet geometry에 적용되는 normalization 계약과 representative extreme meshes에서 measured inlet flux/G를 확인한다.

## Campaign 운영 및 repository quality 관찰

### D-01 — 현재 test baseline은 완전히 green이 아님

- 위치: `tests/test_backfill_run_manifest_fields.py:45`, `:78`; `tests/test_inventory_convergence_classification.py:216`.
- 상태: **EXECUTION-CONFIRMED**.
- 실행: WSL에서 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`.
- 결과: **1056 passed, 3 failed, 1 skipped**, 약 2.33 s.
- 두 실패: `membrane_blocked_area_frac_geometric` 기대값 `0.0`과 실제 `None`의 차이. 한 실패: CSV fields 기대 count `120`, 실제 `123`.
- 무엇이 깨지는가: green baseline이라는 전제가 현재 commit에는 맞지 않는다. 이 세 실패만으로 physics defect를 증명하지는 않는다. 의미 없는 count assertion/오래된 expectation은 실제 field 계약 검증과 다르다.
- 최소 해결: intended schema 의미를 먼저 확정한 다음 expectation을 갱신하고, 실제 required names/units/semantics 및 migration fixture를 검증한다. 이번 pass에서는 tests를 수정하지 않았다.

### D-02 / C-04 — live campaign selection과 runbook 설명 불일치

- 위치: `configs/batch_config.py:297-373`, `:399-450`, `:488-498`; `docs/DEPLOY_RUNBOOK.md` 약 `:91` 및 solver sweep 설명; `docs/PIPELINE_MAP.md` campaign 설명(정밀 line 재확인 필요).
- 상태: **CODE-CONFIRMED** config 내용. 의도적 exploratory selection일 수 있음.
- 관찰: live `mesh_batch_cases`에는 ML 3 + Pillar 9 + Sinusoidal 9 + empty 1 = 22개가 append된다. Diamond tuple은 뒤에 존재하지만 그 list에 append되지 않는다. runbook의 empty list 설명 또는 전체31개 설명과 맞지 않는다. `solver_sweep_cases`는 당시 5개이고 문서의 Diamond 9개 설명과 다르다.
- 무엇이 깨지는가: batch entrypoint가 31×9 campaign을 실행한다고 오해할 수 있다. exploratory config 자체가 bug라는 뜻은 아니나 production coverage gate가 필요하다.
- 최소 해결: campaign matrix를 선언적으로 생성하고 expected `(geo_id, u, p)` set과 정확한 cardinality/uniqueness를 검사한다. exploratory list와 production list를 이름과 entrypoint로 분리하고 docs를 맞춘다.
- 설정 ergonomics 판단: 279개의 8-key dict를 hand-edit하는 방식은 omission/duplicate/parameter drift 검증이 없으면 위험하다. generator와 dry-run plan/coverage report가 가장 작은 개선이다. 기존 orchestrator가 이미 제공하는지 전수 확인은 미완료다.

### D-03 / B-10 — headline metric window와 연구 deliverable window의 계약 확인 필요

- 위치: `docs/metrics_conventions.md`, `docs/POSTPROCESSING_MAP.md`; 최종 CSV/code line 미확인.
- 상태: **CANDIDATE / UNKNOWN**.
- 관찰: docs는 headline LMH/dP full-active span과 evaluation-window 값을 구별한다. 일부 안내는 comparison에 `pressure_drop_spacer`를 사용하라고 설명한다. 사용자 deliverable은 evaluation-window dP/dx다.
- 무엇이 깨지는가: canonical CP는 evaluation window인데 LMH/dP는 다른 spatial scope인 열을 선택할 수 있다. 아직 plot/summary에서 실제 혼용을 확정하지 않았다.
- 최소 해결: 연구 metric → 정확한 CSV column → units → window mapping을 고정하고 plot code가 그것을 사용하도록 검증한다. 물리적으로 정당화된 `n_lead_excluded=3`을 근거 없이 바꾸지 않는다.

### D-04 — documentation/legacy cleanup의 잠정 항목

- 위치: `configs/batch_config.py:173-180`, `:198-200`, 약 `:237`; `docs/RESTRUCTURE_PLAN.md` grid-study 과거 설정 절; `docs/metrics_conventions.md` CP max 변화 설명. 정밀 line 재확인 필요.
- 상태: **PARTIAL**.
- 관찰: batch_config의 과거 positive-z origin 설명과 현재 centered-z 설명이 함께 남아 있다. nominal membrane area를 시사하는 옛 comment와 현재 wetted-area convention도 대조가 필요하다. 역사적 restructure 기록에는 `periodic_after_surface_mesh=False`가 남지만 현재 mandatory True와 시점이 다르다. CP max가 `2.73751→125.245`인데 감소한다고 표현한 문구는 수치 방향과 맞지 않는다.
- 무엇이 깨지는가: historical instructions를 live configuration guidance로 잘못 따를 수 있다. 역사 문서는 superseded 표시가 있으므로 무조건 current bug로 계산하지 않는다.
- 최소 해결: 현재 계약에 맞게 live comments를 정리하고 historical values에 명확한 시점/비권장 표시를 둔다. 숫자 서술 오류를 수정한다.
- README: 읽은 범위에서 공개하면 안 되는 unpublished physics 결과의 확정 사례는 찾지 못했다. geometry/cell counts와 numerical result 공개를 혼동해서 finding을 만들지 않는다. public-facing files 전수 감사는 미완료다.

### C-05 — postprocessing 비용 개선 및 반복 실행 가속의 원인

- 위치: `src/ro/fluent_report_helpers.py`, `scripts/pyfluent_report_extract.py`; report creation 구현 line 검토 미완료.
- 상태: **UNKNOWN — 실제 timing traces 및 PyFluent 0.38 API 확인 미완료**.
- 알려진 내용: 사용자 제공 38.7 min/case, 135 report definitions 생성 736 s, settings-API round trips 약 80%를 존중한다. per-report `set_state` batching 및 mesh별 case에 definitions cache는 검토 후보지만 아직 구현을 확인하지 않았다.
- 최소 다음 조치: report 생성 경로를 읽고 동일 state를 한 번에 설정할 수 있는 최소 patch 범위를 제안한다. cached definitions는 window/layout/physics/naming invalidation 계약을 먼저 확인한다. direct solver launch로 바꾸는 제안은 하지 않는다.
- `1438 → 1091 → 843 s`의 41% monotonic 감소는 세 표본만으로 원인을 확정할 수 없다. filesystem/OS/Fluent/PyFluent warm caches, server load, allocation 등은 가설일 뿐이다. phase-level timings 및 warm/cold 교차 실행이 필요하다.

## 아직 완료하지 않은 전수 감사 목록

1. A: 모든 duplicated value pair와 winner, 모든 success-permitting `except`의 output/gate, 고정 indices/counts, cross-field validator 전수 목록. 위 목록은 발견분만이다.
2. B: `udfs/260822_RO_UDF.c` 1543 lines 전체, report helpers 2580 lines 및 extractor 2538 lines의 CP/dP/LMH CSV 연결; Bae 2023 Eq12와 Liang 원문; Fluent surface-massavg 공식 의미; CP spread caller/gate; 실제 face-to-cell-to-surface aggregation.
3. C: `batch_meshing.py`, `batch_solver_sweep.py`, `batch_solver_rerun.py`, `batch_report_extract.py`, `batch_postprocess_all_cases.py`의 recovery/retry/skip/freshness 전수 감사. manifest 재구축의 artifact 없는 dead end와 equivalente run/report dead ends.
4. D: call graph와 test-only validators, unreachable/dead/duplicate modules, integration/contract/fault-injection gaps, legacy ids/names 전체 검색, public-facing 결과 노출 전수 확인.

## 읽기 진행 기록 — 재개 시 불필요한 반복 방지용

- docs: `docs/AGENTS.md`, `README.md`, `GEOMETRY_DESIGN.md`, `MESH_LANDSCAPE.md`, `DEPLOY_RUNBOOK.md`, `RESTRUCTURE_PLAN.md`, 역사적 `REVIEW.md`는 읽었다. `POSTPROCESSING_MAP.md`, `metrics_conventions.md`는 대부분 읽었으나 이전 큰 출력의 일부 truncation 가능 구간은 필요할 때 확인한다. `PIPELINE_MAP.md:245-285`는 출력 누락 여부 확인 필요.
- src: `manifest.py` 대부분(특히 run references 약 :635 부분 추가 필요), `domain_layout.py` 대부분, `campaign_geometry.py:355-559`, `cp_metrics.py:1-349`, `post_config.py`, `batch_config.py`를 확인했다. `cp_metrics.py:349-440`, registry 전반은 미완료다.
- solver: `:51-153`, config/physics 일부, `:365-409`, `:551-700`, `:730-952` 일부, `:2515-2593`, launch/mesh/physics/termination 주요 부분을 확인했다. 전체를 읽은 것이 아니다. inlet application, source hooking, report setup, iteration gates 추가 필요.
- test 결과는 위 D-01에 고정했다. 이번 체크포인트에서 재실행하지 않았다.

## 존중할 측정 기반 결정

`membrane_blocked_area_frac=0.0`의 wetted-area 근거, flux `(without-sources)`, `periodic_after_surface_mesh=True`, extract의 meshing→`switch_to_solver()`, legacy `u_mean_ms=u_target/G`, 측정된 G 일치, `n_lead_excluded=3`, deterministic re-meshing을 근거 없이 뒤집지 않는다. 이들에 대한 반론에는 해당 측정과 적용 범위를 직접 다루는 추가 증거가 필요하다.

---

이 파일은 interrupted pass의 증거와 검토 진행 상태를 보존한다. 후속 section별 추가 기록은 아래에 append한다.

## Section A 확장 — re-sync 및 A1 source-of-truth sweep

### 기준점 확인

요청에 따라 `git fetch` 및 `git log --oneline -5` 실행. HEAD와 origin/main 모두 `4fc26b776a3dd29a9cce44817ef4ebe714b42e72`. 기존 review base와 diff 없음. **기존 A findings line shift 없음**. 최신 commit 자체가 meshing→switch launch 복원이다. working tree 변경은 미추적 review notes뿐이었다. `git pull`/checkout/source 변경은 하지 않았다.

```text
4fc26b7 Restore meshing-then-switch extract launch; direct solver mode dies on this host with Failed to construct hwtree.
efbced9 Retry transient Fluent extract crashes on a fresh solver session so a socket reset or Scheme heap fault does not lose a run.
f72190f Score dP spread over the layout evaluation window so the WARNING no longer mixes excluded cell 4 with a missing window cell.
6f82a6d Queue the p8M CP-grid pair and the D0817 u=0.3 dip test, and record that dP spread is not a physical number.
c315b49 Warn on evaluation-window dP spread so a PASS gate cannot hide an uninterpretable window mean.
```

### A-02 확장 — source pair 및 precedence inventory

아래는 CODE-CONFIRMED인 코드 계약이다. 실제 mesh/CAD/CSV mismatch 발생 여부는 `C:/ro_data`가 다른 머신에 있으므로 UNKNOWN이다. `필수`는 279-run campaign 또는 해당 산출물을 campaign에 사용하는 경로에서 사전 수정/검증 필요, `유예`는 현재 scalar campaign 경로를 막을 필요 없음이다. 정상적인 override도 누락하지 않고 표시한다.

| 값 / 두 source | 위치 / path별 winner | silent divergence 및 최소 조치 | 시점 |
|---|---|---|---|
| Mesh `spacing_code`, `attack_angle_deg`, `filament_d_m`, `bridge_radius_m`, `overlap_m`, `n_active_cells`, `cell_length_x_m`, `periodic_shift_y_m` | `campaign_geometry.py:483-521`: payload에 있으면 config-derived payload 우선, 없으면 registry. `meshing_code_260616.py:45-83`는 해당 config 값을 payload에 항상 제공 | 값 자체 범위 검사만으로 registry/CAD와 mismatch는 검출 안 됨. resolved geometry 객체를 만들고 registry와 conflict를 명시적으로 허용/거부 | 필수 |
| `periodic_shift_y_m` / Fluent `periodic_shift_y` | `meshing_code_260616.py:60-61`에서 mm→m 변환; 위 config 값 보존. registry source label `derived_from_angle`은 별도 유지 | 예전 overwrite bug는 현재 경로에서 수정됨. 하지만 label만 derived이고 수치 identity를 확인하지 않음 (`manifest_validation.py:183-226`). measured periodic translation과 대조 | 필수 |
| `Sigma_d_nominal_m`, `membrane_trim_m`, `membrane_contact_width_m`, consumed/geometric blocked fraction, ML layer angles/diameters/axes, joint positions/R/ratio/r_min/count, curvature, spacer zones | `campaign_geometry.py:483-521`: registry가 mesh payload를 덮어씀. CAD는 data root의 `.dsco`이고 별도 source | registry 내부 일부 arithmetic gate는 있지만 실제 CAD 측정과 연결 안 됨. config bridge radius와 registry joint R도 독립. resolved metadata와 CAD-derived measurement 계약 필요 | 필수 |
| ML/Pillar attack angle | `batch_config.py:132-155,299-324` 공통 `45`; registry `campaign_geometry.py:325,355-390`은 `0` | mesh payload는 config `45`; geometry registry 설명은 `0`. 이는 현재 코드에서 직접 보이는 metadata divergence. 물리 mesh가 틀렸다는 증거는 아님. family 의미에 맞게 하나의 명시값을 선택 | 필수(metadata) |
| Mesh `porosity_eps` / registry None / run `porosity_eps` | `meshing_code_260616.py:83-88`: measured porosity 최종 우선. `campaign_geometry.py:524-559`: run geometry는 registry 우선 | measured mesh porosity가 run에서는 None으로 기록될 수 있음. run은 mesh metadata를 상속; unknown 값으로 덮어쓰지 않기 | 필수(metadata) |
| Run geometry fields / mesh geometry fields | `solver_code_260616.py:51-105`, `campaign_geometry.py:524-559` | mesh SHA만 연결하고 `_GEOMETRY_FIELDS`는 registry에서 다시 생성. run↔mesh field equality gate 없음. run fields는 mesh에서 상속하고 shared field/hash를 비교 | 필수 |
| `membrane_blocked_area_frac`: mesh vs run | solver `solver_code_260616.py:3155`는 mesh, extract `pyfluent_report_extract.py:1016-1027`는 run | 현재 registry 0이라 정상 fresh manifests는 일치하지만 older/custom run 값은 독립. 특히 run validation은 mesh와 달리 blocked 범위 검사도 빠져 있음. campaign policy `==0.0`와 cross-manifest equality를 live preflight에 적용. geometric footprint와 혼동 금지 | 필수 |
| membrane/buffer/spacer wall lists: CAD/config/mesh/registry/UDF/post | `run_config.py:107-122,254-255`; `meshing_code_260616.py:64-65`; `solver_code_260616.py:2854-2873`; `pyfluent_report_extract.py:841-855`; UDF `:43-44` | solver는 run config, extract는 post config, UDF는 hardcoded membrane names, spacer discovery는 prefix, manifest spacer names는 registry. nonempty이면 일부 누락은 통과 가능. split suffix를 정규화해 actual zones와 선언 집합의 exact coverage 비교 | 필수 |
| Batch common config / per-case dict / base config / env module | `batch_solver_sweep.py:163-168,250-253`; `run_config.py:353-387`; worker config loading | per-case가 common 위에, JSON override가 base 위에 적용. sweep는 leftover alternate config와 skip-validation env를 제거하므로 이 경로 보호됨. direct worker는 env 선택/validation skip 가능. resolved effective config 저장 및 production skip 금지 | 필수(재현성); override 자체는 정상 |
| Post layout override / mesh manifest | `batch_report_extract.py:71-95`, `batch_postprocess_all_cases.py:771-793`; direct extractor `:342-347,939-949` | batch는 manifest→layout override. direct extractor는 사용자가 준 config만 검증하고 actual run mesh와 equality는 확인 안 함. direct path도 manifest를 읽어 동일성 확인 | 필수 |
| Legacy layout aliases / asymmetric fields | `fluent_report_helpers.py:490-602` | asymmetric fields로 계산; `domain_length_m`, `n_unit_cells`, `buffer_length_m`, `n_buffer_cells_each_end`, lead alias가 함께 있으면 conflict raise. 이 pair는 이미 보호됨. 다만 int coercion truncation은 A4 대상 | 정상 |
| `domain_x_min_m` / measured x min | `post_config.py:106`, `run_config.py:327`; `domain_layout.py:315-353`; diagnostics에서 run `.get(... ) or 0.0` | nominal 0 우선, 실제 mesh translation 여부 검사 없음. 현재 origin0은 명시적 설계이므로 바꾸지 말고 measured origin gate 추가 | 필수 검증 |
| Mesh rebuild: existing manifest / mesh log / current registry | `rebuild_mesh_manifest.py:169-183,293-342` | existing→non-None log overrides→builder→registry overlay. `inlet_profile_G`, creation/version은 existing 보존 | SHA check와 explicit `--allow-field` diff gate가 있어 완전히 silent하지 않음. 현재 registry가 과거 CAD를 설명한다는 보장은 별개. provenance와 measurement 확인 후 허용 | 현재 gate 보존 |
| `u_target_ms`, `p_gauge_pa` / `run_id` / config / actual BC | `solver_code_260616.py:51-84`; `manifest.py:423-482`; `batch_report_extract.py:64-79` | config→run, `run_id`는 별도 string. batch_report caller가 u/p를 주면 run 값을 덮어씀; all_cases post는 run 우선 | name↔parameter arithmetic check 없음. u/p와 run_id를 대조하고 post caller override conflict 거부 | 필수 |
| `u_mean_ms` / `u_target_ms/G` / stored mesh G / current transcript G | `solver_code_260616.py:87-134,3708-3742`; `manifest_validation.py:414-431` | 기존 u_mean non-None이면 보존. G는 transcript agreement 및 mesh update 경로; source mesh_id equality는 검사 | u_mean*G=u_target 검사는 없음. 기존 값이 잘못되어도 보호/보존 가능. legacy coefficient 의미 그대로 arithmetic check 추가; 값의 정의를 physical mean으로 바꾸지 않음 | 필수(metadata) |
| UDF constants / config / actual material / post config | `run_config.py:263-275`, `post_config.py:72-76`, UDF `:47-49,94,193-194`; solver `:551-599,2978-3015` | UDF는 MW/RHO/C_INLET_REF/D_SALT/B_perm 자체 상수. patch는 SALT_YI_INDEX,U_TARGET만. Fluent diffusivity는 config와 assert; actual density/viscosity는 print만. post는 post config | salt_mass_fraction override를 바꿔도 C_INLET_REF는 고정. MW unit conversion, c0=rho*Yi/MW, UDF D/B와 effective config 계약 필요. static parity tests는 runtime override를 보호하지 않음 | 필수 |
| `mixture_name` config / Fluent mixture list | solver `:2990-2998` | hardcoded mixture-template, 없으면 첫 mixture가 config를 이김 | 실제 fluid zone assigned material과 비교 없음. name existence가 아니라 assignment/material state를 assert | 필수 |
| UDM semantics: Python module / config udm_count / case-local UDF / compiled library | `udm_layout.py:15-43,158-188`; `post_config.py:83-97`; extractor `:901`; `run_config.py:275` | main extract는 Python canonical indices; field-check는 case-local enum. source parity test는 current dated UDF만 보호 | archived enum도 field names가 존재하면 잘못된 물리량을 읽을 수 있음. extract도 case-local enum/manifest UDF version/hash와 실제 allocation을 검증. current production13에는 static agreement 있음 | 필수 compatibility gate |
| `analytic_cwall` manifest / case-local source / repository source | solver `:108-134`; `manifest.py:718-738`; `udm_layout.py:191-225` | solver는 case-local 없으면 master fallback. extract sync는 case-local parsed value 우선 시도하나 protected conflict는 reject | compiled library가 source와 같은지 증명하는 hash 없음. current actual build state는 UNKNOWN. compile/source fingerprint 기록 | 필수 provenance |
| ramp iteration / convergence safety config | UDF `:59-66`; `run_config.py:289-310` | UDF는150; Python safety는 별도 config150+50 및 QoI ignore200 | static current 값은 부합. overrides로 source ramp 전에 stop을 허용할 수 있는 계약 검증 부재. parse UDF ramp and require safety minimum | 필수 override gate |
| convergence target/cap / current batch defaults / run settings / transcript | `solver_common.py:198-244`; `case_inventory.py:413`; `residual_measurement_report.py:62`; `convergence_quality.py:31-34,131-184` | reporting CLI/current batch가 fallback 기준, quality는 fixed thresholds, inventory는 transcript/summary를 사용 | run별 residual target와 reporting 기준이 달라도 구별 약함. numerical-stop, post-quality 기준은 다른 개념이므로 합치지 말고 각 effective threshold/source를 기록 | 필수 판정 provenance |
| c_b: summary CSV / raw JSON; contour k: raw JSON partial key intersection | `pyensight_contour_export.py:3405-3505` | c_b는 CSV 먼저, 없으면 JSON. k는 available k/area key intersection | 서로 다른 extract의 stale files가 섞여도 timestamp/hash/evaluation-cell exact set gate 없음. extract identity 검증 및 exact window coverage 요구 | 그림을 비교에 쓰기 전 |
| contour reconstructed physics / UDF | `pyensight_contour_export.py:183-194,3162-3383`; post mu / `.scm` divisor (`shear_cff_mu_guard.py:33-82`) | direct contour는 자체 constants; shear는 CFF 또는 tau/config mu. mu guard read failure는 skip | numerical table과 다른 계산으로 그려질 수 있음. formula/source/units를 기록하고 mismatched mu/constants는 fail | 그림을 비교에 쓰기 전 |
| final file identity: canonical builder / direct absolute overrides | `pyfluent_report_extract.py:217-296,399-413,804-806`; `batch_postprocess_all_cases.py:742-768` | batch는 canonical names; direct는 absolute configured files 허용. extractor는 configured dat 존재 확인 후 `read_case_data(case)` 실행 | 존재를 확인한 dat가 실제 auto-loaded companion dat라는 보장 없음. case/data parent+stem+manifest identity 확인 또는 explicit read_case/read_data | 필수 |
| accepted/completed: artifacts / manifest state / hand-maintained non_converged list | `batch_solver_sweep.py:203-217`; `batch_report_extract.py:550-583` | sweep skip는 file pair 존재 우선. opt-in merged summary는 blacklist에 없으면 `use_for_final_comparison=True` | 빈/old pair, FAIL/UNKNOWN 품질을 accepted로 오인할 수 있음. skip와 merge에 current manifest+finite metric+quality gate 사용 | 필수 |

이 표는 A-02를 확장한 것이며 기존 finding을 새로 발견한 것처럼 중복 계산하지 않는다. B의 scientific adequacy나 C의 retry 설계 리뷰를 완료했다는 뜻도 아니다.
