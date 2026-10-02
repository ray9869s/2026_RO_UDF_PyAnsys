# Extract outputs — `summary_metrics_wide.csv`가 실제로 담는 것

이 문서는 `scripts/pyfluent_report_extract.py`와
`src/ro/fluent_report_helpers.py`가 **지금 코드에서** 계산하는 값의
source of truth다. 캠페인 분석은 여기 적힌 열 이름·범위·정의를 따른다.
식별자·수식·단위는 번역하지 않는다.

보호 결정 (이 문서가 뒤집지 않음): `membrane_blocked_area_frac=0.0` (consumed
LMH 분모), flux `(without-sources)`, `n_lead_excluded=3`.

---

## Extract가 끝나면 남는 파일

한 leaf의 산출물은 `runs/{family}/{geo_id}/{mesh_id}/{run_id}/post/reports/`
아래에 있다.

| 파일 | 역할 |
|---|---|
| `summary_metrics_wide.csv` | 캠페인 비교용. 한 행, 메트릭이 열. 아래 열 지도의 대상. |
| `summary_metrics.csv` | 같은 내용의 long format (`metric` / `value` / `unit`). |
| `mass_balance.csv` | 질량 닫힘만. |
| `pressure_report.csv` | 압력과 unit-cell 평면 dP. |
| `wall_shear_report.csv` | 막 wall shear. |
| `raw_report_values.json` | Fluent raw compute, layout, segmented CP 전체 dict, timing 보조. |
| `report_extract_timing.json` | phase 벽시계. `failed_phase`는 성공이면 `None`. |
| `extract_source.json` | R-05 skip 증거 (`final_case_sha256`, `final_data_sha256`, `solver_attempt_id`). |

성공 시 run `manifest.json`에 `convergence_quality`가 추가로 쓰인다. 이 게이트는
`stop_reason`과 독립이다.

`compute_cp_spread`의 기본값은 `configs/post_config.py`에서 `False`다. 켜도
`cp_canon_window_avg`는 바뀌지 않는다 (`docs/metrics_conventions.md`). 이
문서의 p6M extract는 그 기본값이다.

---

## 1. Phase별 계산과 CSV 열

`n_total = n_buffer_in + n_active + n_buffer_out`. 7-active 표준 레이아웃은
`1+7+2`이므로 `n_total = 10`. Diamond는 `n_active`만 바뀌고 buffer는 같다.

### `cell_7_report_creation`

Fluent report **정의**와 평면/iso-surface만 만든다. compute 없음.

고정 11개:

- flux: `pp_m_in`, `pp_m_out` (경계 mass flow, without-sources가 물리값)
- 막 면적: `pp_area_mem` (`surface-area`, active membrane)
- LMH 식: `pp_lmh_mass_balance`, `pp_lmh_mass_balance_signed`
- 입·출구 압력: `pp_p_in_avg`, `pp_p_out_avg`, `pp_pressure_drop`
- spacer 끝단 평면: `pp_p_spacer_in_avg`, `pp_p_spacer_out_avg`,
  `pp_pressure_drop_spacer`

unit-cell 경계 `n_total + 1`개 (`range(n_total+1)`). 경계마다 4개:

- `pp_p_unit_cell_boundary_{i}_avg` — `surface-areaavg` pressure
- `pp_salt_mass_fraction_unit_cell_boundary_{i}_massavg` — `surface-massavg` `nacl`
- `pp_area_unit_cell_boundary_{i}` — `surface-area`
- `pp_salt_mass_fraction_unit_cell_boundary_{i}_avg` — `surface-areaavg` `nacl`

이 4개가 레이아웃이 커질 때 늘어나는 **extra**다.

막 표면 18개 (active membrane, `surface-areaavg` / `facetmax` / `facetmin`):
`pp_jw_*` (`udm-6`), `pp_cm_*` (`udm-7`), `pp_lmh_udm_*` (`udm-8`),
`pp_cp_inlet_*` (`udm-9`), `pp_salt_flux_*` (`udm-10`), `pp_wall_shear_*`
(`wall-shear`).

체적 3개 (fluid zones): `pp_volint_salt_mass_source` (`udm-0`),
`pp_volint_total_mass_source` (`udm-1`), `pp_udm_area_sum` (`udm-11`).

**개수 공식** (`expected_cell_7_report_names`):

```
N_cell7 = 36 + 4 * n_total
        = 11 + 4*(n_total + 1) + 18 + 3
```

| 레이아웃 `n_total` | 이름 수 | 예 |
|---:|---:|---|
| 10 (`1+7+2`) | 76 | REF, D2450_a45, Pillar, Sin, ML |
| 18 (`1+15+2`) | 108 | D0817_a60 |
| 24 (`1+21+2`) | 132 | D0817_a45 |
| 30 (`1+27+2`) | 156 | D0817_a30 |

Cell 7 이름은 나중에 `csv_write`에서 복사되거나 Python으로 파생된다. 이
phase 자체는 CSV를 쓰지 않는다.

### `cell_8_compute`

Cell 7 정의를 순서대로 `compute`한다.

- `pp_m_in` / `pp_m_out`: Fluent flux 분해. CSV `m_in` / `m_out`은
  `(without-sources)` 성분. 같이 `m_in_with_sources`, `m_in_mass_source`
  (outlet 대칭)를 채운다.
- 나머지: 스칼라 하나 → 같은 이름의 `computed_values`, 이후 CSV 별칭.

load-bearing 정의의 compute 실패는 extract를 죽인다
(`LOAD_BEARING_REPORT_NAMES`).

### `cell_8_25_salt_reduction`

`solver.fields.reduction` on fluid zones. 종은 `nacl`.

CSV: `pp_salt_mass_fraction_max` / `_min`,
`pp_salt_mass_fraction_cells_above_threshold` (기본 0.99),
`pp_salt_mass_fraction_cells_below_threshold` (기본 1e-6).
실패해도 extract는 계속되고 `concentration_diagnostic_error*`만 채운다.
**캠페인 CP/dP/LMH에 쓰이지 않음.**

### `cell_8_4_midplane_cb`

채널 중앙 평면 `z = 0.5*(z_min+z_max)` (캠페인 mesh에서 `z = 0`)를 만들고,
active cell마다 x-clip 한 뒤 `surface-massavg`로 염을 읽는다. 호출은
`spacer_cells` = 모든 active cell이다. window 평균만 evaluation cell로
다시 집계한다.

CSV: `pp_c_b_midplane_cell_{N}_mol_m3` (active cell마다),
`c_b_window_mol_m3` (evaluation window, mid-plane **면적** 가중),
`c_b_window_salt_field`, `c_b_window_is_mass_fraction_field`.

같은 phase의 8.4b: 도메인 전체 mid-plane `surface-areaavg` →
`c_bulk_center_*`. 입구 buffer를 포함한다. **CP 분모가 아니다.**

### `segmented_membrane_cp`

막 wall을 unit-cell x-구간으로 iso_clip하고, 구간마다 UDM 9개 리포트를
만든다 (아래 §2). Python이 `k_N`과 window 집계를 한다.

캠페인 열: `cp_canon_window_avg` / `_max`, `cp_L1_window_*`,
`cp_L2_window_*`, `cp_*_all_active_*`, per-cell `pp_cp_*_cell_{N}`,
`pp_membrane_area_cell_{N}_m2`, `pp_cm_mol_m3_cell_{N}`,
`pp_jw_m_per_s_cell_{N}`, `pp_cp_perm_mol_m3_cell_{N}`,
`pp_cp_canon_rescale_k_cell_{N}`, wall 분해
`cp_canon_lower_window_*` / `cp_canon_upper_window_*`.

없거나 실패하면 extract는 `RuntimeError`로 죽는다. `cp_inlet_avg`(L2)는
대체가 아니다.

### `csv_write`

Cell 8.5 단위 변환, summary row 조립, 위 CSV/JSON 기록, R-05 sidecar,
manifest `convergence_quality`. 새 Fluent 리포트는 없다.

---

## 2. Segmented CP — 구간당 9 리포트와 wall 분해

`_compute_membrane_segment_via_iso_clip`이 한 구간에 만드는 Fluent 이름은
`tag`로만 갈린다. combined는 `comb_{N}`, 하막은 `w_lower_{N}`, 상막은
`w_upper_{N}`. 그래서 `pp_mem_cp_w_lower_5`, `pp_mem_cp_max_w_lower_5`가
보인다. 이 9개는 **CSV 열 이름이 아니다.** compute 후 삭제된다. CSV는
아래 오른쪽 열이다.

| # | Fluent 이름 | 타입 | 필드 | CSV (combined `comb_N`) |
|---|---|---|---|---|
| 1 | `pp_mem_area_{tag}` | `surface-area` | (없음) | `pp_membrane_area_cell_{N}_m2` |
| 2 | `pp_mem_cm_{tag}` | `surface-areaavg` | `udm-7` (Cm) | `pp_cm_mol_m3_cell_{N}` |
| 3 | `pp_mem_jw_{tag}` | `surface-areaavg` | `udm-6` (Jw) | `pp_jw_m_per_s_cell_{N}` |
| 4 | `pp_mem_cp_{tag}` | `surface-areaavg` | `udm-9` (L2 CP) | `pp_cp_inlet_unit_cell_boundary_{N}` = `pp_cp_L2_cell_{N}`; × `k_N` → `pp_cp_canon_cell_{N}` |
| 5 | `pp_mem_cp_max_{tag}` | `surface-facetmax` | `udm-9` | × `k_N` → `pp_cp_canon_max_cell_{N}`; 그대로 `pp_cp_L2_max_cell_{N}` |
| 6 | `pp_mem_cm_max_{tag}` | `surface-facetmax` | `udm-7` | `/ c_b` → `pp_cp_L1_max_cell_{N}` |
| 7 | `pp_mem_cm_min_{tag}` | `surface-facetmin` | `udm-7` | `cm_min_raw_cell_{N}` / `cm_min_used_cell_{N}` (spread 가드) |
| 8 | `pp_mem_jw_max_{tag}` | `surface-facetmax` | `udm-6` | spread의 `c_p,min` (facetmax Jw) |
| 9 | `pp_mem_jw_min_{tag}` | `surface-facetmin` | `udm-6` | `jw_min_raw_cell_{N}` / `jw_min_used_cell_{N}` |

`c_p`는 UDM 슬롯이 아니다. Python `film_theory_cp_perm_mol_m3`:
`c_p = B * c_m / (J_w + B)` (`B = 2.50e-8` m/s). 평균 경로에서는
segment 평균 `c_m`, `J_w`로 한 값 → `pp_cp_perm_mol_m3_cell_{N}`.

`compute_cp_spread=False`(현재 기본)여도 9개는 만들어진다. 7–9는 가드
bisection에 쓰이지 않고, `cp_canon_rescale_delta_max`는 null,
`cp_canon_rescale_delta_status=not_evaluated`다. `k_N`은 평균 `c_p`로
여전히 계산·적용된다.

### 구간 수 (`N = n_active`)

`spacer_cells` = 모든 active cell (1-based global:
`n_buffer_in+1` … `n_buffer_in+N`). 표준 `1+N+2`에서 cell
`2 … N+1`.

evaluation window: `n_lead_excluded=3`, `n_trail_excluded=0`이면
`N_eval = N - 3`.

1. **combined** (`tag_prefix=comb`): 양쪽 막을 한 clip에. **N개**
   (active 전부, lead cell 포함).
2. **per-wall** (`w_lower` / `w_upper`): evaluation cell만.
   **2 × N_eval**. combined의 `k_N`을 재사용하고 quantile bisection은
   하지 않는다.

합계 `N + 2(N-3) = 3N - 6` (trail=0, lead=3). 구간당 9 정의이므로
총 생성 수는 `9(3N-6)`.

| geo | `N` | 구간 | 정의 수 (9×구간) |
|---|---:|---:|---:|
| 7-active | 7 | 15 | 135 |
| D0817_a60 | 15 | 39 | 351 |
| D0817_a30 | 27 | 75 | 675 |

### 상·하막이 최종 열로 합쳐지는 방식

**per-cell / window 캠페인 값은 상·하를 나중에 평균하지 않는다.** combined
clip이 두 막을 한 표면으로 두고, Fluent `surface-areaavg`가 그 clip의
**막 면적**으로 가중한다. `cp_canon_window_avg`는 evaluation cell의
combined `pp_cp_canon_cell_{N}`을 다시
`pp_membrane_area_cell_{N}_m2`(양쪽 막 합)로 면적 가중한다.

per-wall 열 `cp_canon_lower_window_avg` / `cp_canon_upper_window_avg`는
같은 `k_N`을 각 막 UDM-9 평균에 곱한 뒤, **그 막만의** clip 면적으로
window 평균한 분해다. 캠페인 ranking에 쓰지 않는다.

`c_b_window_mol_m3`의 가중은 막 면적이 아니라 **mid-plane clip 면적**
`midplane_area_by_cell`이다.

---

## 3. Canonical CP 체인 (UDF 얼굴 → CSV)

캠페인 정의 (Bae 2023 un-approximated):
`M = (c_m - c_p) / (c_b - c_p)`.
UDM-9가 저장하는 것은 이 식이 아니라 inlet 분모 L2:
`M_L2 = (c_m - c_p) / (c_0 - c_p)`, `c_0 = C_INLET_REF = 597.8268309 mol/m3`.

1. **막 face (UDF `260822_RO_UDF.c`)**  
   인접 셀 `Yi` → `c_1 = rho_ref * Yi / MW`. `RO_ANALYTIC_CWALL=1`이면
   film으로 `c_m`을 재구성한 뒤 quadratic을 한 번 더 푼다.
   `c_p = B c_m / (J_w + B)`, `J_w`는 un-ramped 물리 flux.
   `cp_face = ro_compute_cp` = L2.

2. **Face UDM (`F_UDMI`)**  
   `udm-6/7/8/9/10`에 face 값을 쓸 수 있으나 기본
   `RO_UDM_FACE_DIAGNOSTICS=0`. UDF 주석: Fluent 25.1 contour/surface
   report는 `F_UDMI`를 읽지 않는다.

3. **Cell UDM (`C_UDMI`, 생산 경로)**  
   인접 fluid cell에 `value * dA`를 누적한 뒤 `UDM_AREA`(`udm-11`)로
   나눈다. 셀 값은 **face에서 이미 만든 M_L2의 면적 가중 평균**이다
   (평균 후 CP가 아님).

4. **Fluent 표면 리포트**  
   iso_clip된 막에서 `surface-areaavg` / `facetmax`가 `udm-9`를 읽는다.
   UDF가 확인한 대로 표면 적분은 **cell UDM**을 본다. 셀 평균을 벽
   표면으로 한 번 더 보간·면적 가중하는 단계다. 코드는 이 값이 순수
   per-face 평균과 같은지 검사하지 않는다. Astra B-03이 미검증으로 둔
   지점이 여기다.

5. **Python 스칼라 rescale**  
   `k_N = (c_0 - c_p,avg,N) / (c_b,N - c_p,avg,N)`.
   `M_canon,N = M_UDM9,N * k_N`.
   `c_p,avg`는 segment 평균 `c_m`, `J_w`의 film-theory (face별 `c_p`가
   아님). face마다 `k`가 달라지는 오차는 `delta` 가드가 묶지만, 기본
   extract는 spread를 평가하지 않아 `delta`가 null이다.
   **두 번째 근사: per-cell `k_N`.** per-face canonical은 `F_UDMI`를
   읽을 수 없어 막혀 있다 (`src/ro/cp_metrics.py`).

6. **Window**  
   `cp_canon_window_avg` = evaluation cell의 `M_canon,N`을 막 면적 가중.
   `cp_canon_window_max` = evaluation cell facetmax의 최댓값 (같은
   `k_N`). 평균의 비가 아니라 **비의 평균을 면적 가중**한 것이다.

중간 CSV 열, 순서대로:

`pp_cm_mol_m3_cell_N`, `pp_jw_m_per_s_cell_N`,
`pp_cp_perm_mol_m3_cell_N`, `pp_cp_inlet_unit_cell_boundary_N` (= L2),
`pp_c_b_midplane_cell_N_mol_m3`, `pp_cp_canon_rescale_k_cell_N`,
`pp_cp_canon_cell_N` → `cp_canon_window_avg`.

전체 막 L2 `cp_inlet_avg`(Cell 7, window 아님)는 이 체인의 대체가 아니다.

---

## 4. `c_b` 분모

| 항목 | 코드가 하는 일 |
|---|---|
| 평면 | `create_channel_midplane_plane`: fluid-zone z reduction으로
  `z_mid = 0.5*(z_min+z_max)`. 캠페인 mesh에서 0. 후보 리스트 없음. |
| clip | active cell마다 x ∈ [경계 N-1, 경계 N] iso_clip. |
| 리포트 타입 | **`surface-massavg`** (`_SURFACE_MASS_WEIGHTED_AVG`). 필드 후보는
  `nacl`, `mass-fraction-of-nacl`, `yi-0`, `species-0`, `udm-7`. 보통
  `nacl` mass fraction → `rho/MW`로 mol/m³. |
| 가중 | Python은 Fluent에 `surface-massavg`만 요청한다. mass flux가
  **평면 법선 (z)** 인지 **streamwise (x)** 인지는 이 저장소의 Python/UDF에
  없다. Astra B-01은 코드만으로 닫히지 않는다. |
| 교차검증 | evaluation cell의 mid-plane `c_b`를, 같은 cell을 끼는 두
  **x-normal** unit-cell `surface-massavg`의 산술 평균과
  `rel_tol=0.005`로 비교 (`assert_midplane_c_b_matches_boundary_mixing_cup`).
  벽면으로 올라간 평면은 여기서 죽는다. x-normal cup 역시 같은
  `surface-massavg` API다. |
| window 열 | `c_b_window_mol_m3`: cell `c_b`를 **mid-plane 면적**으로 가중. |
| 쓰지 말 것 | `c_bulk_center_*` (whole-domain `surface-areaavg`), Cell 7의
  unit-cell `surface-areaavg` salt. |

주석은 이것을 mixing-cup이라고 부른다. 검증 가능한 사실은 요청 타입이
`surface-massavg`이고, x-normal 같은 타입 평균과 0.5% 안에서 맞는다는
것이다.

---

## 5. 열 지도 (`summary_metrics_wide.csv`)

범위: **full** = 도메인 또는 양쪽 막 전체 active span (buffer 포함일 수
있음). **active** = 막 있는 spacer 길이. **window** = evaluation cells
(`n_lead_excluded=3` 이후). **cell** = unit cell 하나.

캠페인 결론 열은 `docs/metrics_conventions.md`의 spacer 비교 규칙이다:
쓰라고 한 것만 **yes**.

### LMH

| 열 | 단위 | 범위 | 캠페인 결론 | 비고 |
|---|---|---|---|---|
| `lmh_mass_balance` | LMH | active 막 면적 | secondary | `\|m_in+m_out\|/(ρ A_mem (1-f_blocked))×3.6e6`. `f_blocked=0`. without-sources. |
| `lmh_mass_balance_signed` | LMH | 같음 | diagnostic | Fluent 부호 있는 식. |
| `lmh_mass_balance_signed_python` | LMH | 같음 | load-bearing | Python `(m_in+m_out)/(ρ A_mem)×MS_TO_LMH`. |
| `lmh_udm_avg` / `_max` / `_min` | LMH | full active 막 | secondary / diagnostic | `udm-8` areaavg/facet. window 아님. |
| `lmh_difference_mass_balance_minus_udm` | LMH | — | diagnostic | |
| `lmh_relative_difference` | — | — | quality gate | `convergence_quality`에 쓰임. spacer ranking 아님. |
| `jw_avg` / `_max` / `_min` | m/s | full active 막 | diagnostic | `udm-6`. |

### CP

| 열 | 단위 | 범위 | 캠페인 결론 | 비고 |
|---|---|---|---|---|
| **`cp_canon_window_avg`** | — | window | **yes** | 캠페인 비교 정의. |
| `cp_canon_window_max` | — | window | **no** | metrics: 단일 저-Jw face. iteration에 민감. |
| `cp_L1_window_avg` / `_max` | — | window | no | Gu 2017. 정의 민감도 표만. |
| `cp_L2_window_avg` / `_max` | — | window | no | UDM-9 그대로 (inlet `c_0`). |
| `cp_canon_all_active_*` 등 | — | active | no | window 민감도. |
| `cp_canon_lower_window_*` / `upper_*` | — | window, 한 막 | no | wall 분해. |
| `pp_cp_canon_cell_{N}` | — | cell | 구성 요소 | window 평균의 입력. 단독 ranking 아님. |
| `pp_cp_L1_cell_{N}` / `pp_cp_L2_cell_{N}` | — | cell | no | |
| `pp_cp_canon_max_cell_{N}` 등 | — | cell | no | |
| `pp_cp_canon_rescale_k_cell_{N}` | — | cell | 구성 요소 | |
| `pp_cp_canon_rescale_delta_cell_{N}` | — | cell | diagnostic | 기본 extract는 null. |
| `cp_canon_rescale_delta_max` / `_status` / `cp_scalar_rescale_guard_threshold` | — | window | diagnostic | |
| `cp_inlet_avg` / `_max` / `_min` | — | **full** active 막 | **no** | Cell 7 L2. window 아님. canonical 대체 금지. |
| `cm_avg` / `cm_mol_m3_avg` (동일) `_max` `_min` | mol/m3 | full active 막 | grid only | spacer ranking 아님. metrics: grid 판정용. |
| `pp_cm_mol_m3_cell_{N}` | mol/m3 | cell | 구성 요소 | |
| `c_b_window_mol_m3` | mol/m3 | window | sanity | canonical 분모의 window 집계. |
| `pp_c_b_midplane_cell_{N}_mol_m3` | mol/m3 | cell (active 전부) | 구성 요소 | |
| `c_bulk_center_*` | 혼재 | **whole domain** | **no** | inventory only. |
| `pp_cp_perm_mol_m3_cell_{N}` | mol/m3 | cell | 구성 요소 | |
| `pp_cp_bulk_unit_cell_boundary_{N}` | — | cell | diagnostic | `cm_avg / mixing-cup mol` (x-normal). canonical 아님. |
| `c_inlet_ref_mol_m3` | mol/m3 | — | 상수 | 597.8268309. |
| `compute_cp_spread` | — | — | 설정 | |
| `cm_min_*` / `jw_min_*` / `cm_q_*` / `jw_q_*` / `cp_facet_min_rejected_cell_{N}` | 혼재 | cell | diagnostic | |
| `cpc_window_avg_area` / `_flux` | — | window | **no** | 농도통계. canonical 대체 아님. §6. |
| `cp_q999_window_area` / `_flux` | — | window | **no** | \(Q_{0.999}(c_m)\). canonical max 대체 아님. |
| `cp_q99_window_area` / `_flux` | — | window | **no** | \(Q_{0.99}(c_m)\). |
| `cm_area_mean_window` / `cm_q999_window` / `cm_q99_window` | mol/m3 | window | diagnostic | §6 입력. |
| `cp_ref_area` / `cp_ref_flux` | mol/m3 | window | diagnostic | 면적 평균 / \(J_w>0\) 유량 가중 \(c_p\). |
| `n_faces_jw_nonpositive` / `area_jw_nonpositive` | —, m2 | window | diagnostic | \(J_w\le 0\). |
| `cpc_cell_{N}_avg_*` / `cp_q999_cell_{N}_*` / `cp_q99_cell_{N}_*` | — | cell | diagnostic | 같은 식, cell \(c_b\). window 분위의 평균이 아님. |
| `cm_*_cell_{N}` / `cp_ref_*_cell_{N}` / `n_faces_jw_nonpositive_cell_{N}` / `area_jw_nonpositive_cell_{N}` | 혼재 | cell | diagnostic | |
| `turbulence_metrics_status` | — | fluid | diagnostic | `laminar`, `legacy_manifest_no_viscous_model`, `computed`. §7. |
| `viscosity_ratio_max` / `_volavg` | — | fluid | diagnostic | laminar·legacy는 null. |
| `diff_nacl_max` / `_volavg` | m2/s | fluid | diagnostic | |
| `diffl_nacl_min` / `_max` | m2/s | fluid | diagnostic | `mass_diffusivity`와 상대 1e-9. |
| `diff_ratio_max` / `_volavg` | — | fluid | diagnostic | `diff_nacl / mass_diffusivity`. |

### Pressure drop

| 열 | 단위 | 범위 | 캠페인 결론 | 비고 |
|---|---|---|---|---|
| **`pressure_drop_spacer`** | Pa | active span | **yes** | spacer 끝단 평면 areaavg ΔP. |
| `pressure_drop_spacer_per_m` | Pa/m | active | yes에 가깝 | `/ spacer_length_m`. |
| `pressure_drop` | Pa | inlet→outlet | no | buffer 포함. |
| `pressure_drop_per_m` | Pa/m | full `domain_length_m` | no | |
| `p_in_avg` / `p_out_avg` | Pa | inlet/outlet | 구성 요소 | |
| `p_spacer_in_avg` / `p_spacer_out_avg` | Pa | active 끝 | 구성 요소 | |
| `pp_p_unit_cell_boundary_{i}_avg` | Pa | 평면 i | 구성 요소 | |
| `pp_pressure_drop_cell_{N}` | Pa | cell (active) | diagnostic | `p_{N-1}-p_N`. 합은 spacer ΔP와 대수적 항등. |
| `pp_pressure_drop_periodic_per_m` | Pa/m | lead 제외 active | diagnostic | |
| `pp_pressure_drop_cell2_over_cell3` | — | cell 2/3 | diagnostic | Diamond u=0.3 dip 잔재. |

### Mass closure

| 열 | 단위 | 범위 | 캠페인 결론 | 비고 |
|---|---|---|---|---|
| `m_in` / `m_out` | kg/s | 경계 | 구성 요소 | without-sources. |
| `m_in_with_sources` / `m_out_with_sources` / `m_*_mass_source` | kg/s | 경계 | diagnostic | R-12 항등식. |
| `boundary_permeate_mass_flow` | kg/s | — | 구성 요소 | `abs(m_in+m_out)`. |
| `salt_sink_volume_integral_UDM0` | kg/s | fluid | 구성 요소 | `udm-0`. |
| `total_sink_volume_integral_UDM2` | kg/s | fluid | 구성 요소 | `udm-1` (이름 UDM2는 스키마 잔재). |
| `water_sink_volume_integral_UDM1` | kg/s | fluid | 구성 요소 | total − salt. UDM_SM 없음. |
| `mass_balance_error_boundary_minus_total_sink` | kg/s | — | quality | |
| `mass_balance_relative_error` | — | — | quality gate | |
| `salt_flux_avg` / `_max` / `_min` | kg/m2/s | full 막 | diagnostic | `udm-10`. |
| `pp_salt_mass_fraction_*` (reduction) | 혼재 | fluid cells | diagnostic | Cell 8.25. |
| `pp_salt_mass_fraction_unit_cell_boundary_{i}_avg` | mass_fraction | 평면 | diagnostic | areaavg. mixing-cup 아님. |
| `pp_salt_mass_fraction_unit_cell_boundary_{i}_massavg` | mass_fraction | 평면 | 교차검증 | x-normal `c_b` 가드. |
| `pp_salt_mass_fraction_rise_cell_{N}` | mass_fraction | cell | diagnostic | areaavg 차분. |

### Geometry / 막 면적 / shear

| 열 | 단위 | 범위 | 캠페인 결론 | 비고 |
|---|---|---|---|---|
| `area_mem` | m2 | active 막 | 구성 요소 | Fluent `surface-area`. LMH 분모. |
| `pp_udm_area_sum` | m2 | fluid UDM-11 | identity | `area_mem`과 rel 1e-9. |
| `pp_membrane_area_cell_{N}_m2` | m2 | cell, 양 막 | 구성 요소 | CP window 가중. |
| `pp_area_unit_cell_boundary_{i}` | m2 | 평면 | diagnostic | |
| `domain_length_m` | m | full | 구성 요소 | |
| `spacer_length_m` / `spacer_x_in_m` / `spacer_x_out_m` | m | active | 구성 요소 | |
| `pp_unit_cell_boundary_{i}_x_m` | m | 평면 | 구성 요소 | |
| `wall_shear_*` / `wall_shear_rate_*` | Pa, 1/s | full 막 | 이 캠페인 CP 결론에는 안 씀 | rate = shear / `mu`. |

`geo_name` / `case_name`과 `*_error*` / `*_json` / `*_diagnostic*` /
`report_definition_errors_json`은 provenance·실패 흔적이다. 물리 결과가
아니다.

### `LOAD_BEARING_SUMMARY_METRICS`

extract가 성공하려면 이 열들이 비어 있으면 안 된다. 전부 캠페인
*비교 결론*인 것은 아니다. 닫힘·LMH 교차·canonical CP·spacer ΔP를
한 묶음으로 지킨다:

`m_in`, `m_out`, `boundary_permeate_mass_flow`, `area_mem`,
`pp_udm_area_sum`, `lmh_mass_balance`, `lmh_udm_avg`,
`lmh_mass_balance_signed_python`, `lmh_relative_difference`,
`mass_balance_relative_error`, `mass_balance_error_boundary_minus_total_sink`,
`total_sink_volume_integral_UDM2`, `salt_sink_volume_integral_UDM0`,
`water_sink_volume_integral_UDM1`, `pressure_drop`, `pressure_drop_per_m`,
`pressure_drop_spacer`, `pressure_drop_spacer_per_m`,
`pp_pressure_drop_periodic_per_m`, `domain_length_m`, `spacer_length_m`,
`c_b_window_mol_m3`, `cp_canon_window_avg`.

---

## 분석 시 한 줄

Spacer 사이 비교는 `cp_canon_window_avg`와 `pressure_drop_spacer`(또는
`pressure_drop_spacer_per_m`)만 쓴다. LMH는 `lmh_mass_balance` /
`lmh_mass_balance_signed_python`으로 닫힘을 보고, `cp_canon_window_max`·
`cp_inlet_avg`·`c_bulk_center_*`·`cp_L1_*`·`cp_L2_*`로 ranking하지 않는다.
`c_b`가 `surface-massavg`인 것은 코드로 확인되고, 그 가중이 z-법선
질량유량인지 아닌지는 이 코드가 증명하지 않는다.
`cpc_window_avg_*`와 `cp_q*_window_*`로 ranking하지 않는다.

---

## 6. Concentration-statistics CP

기존 CP 열은 그대로다. 이 열은 load-bearing이 아니고, laminar를 포함한
모든 run에서 계산한다. 정의는 `docs/metrics_conventions.md`. 구현은
`src/ro/cp_concentration_stats.py`와 extract의
`collect_concentration_cp_faces`.

입력은 evaluation window의 양쪽 막을 한 x-clip으로 자른 face다. `udm-7`,
`udm-6`은 `boundary_value=False`라 인접 셀 UDM이다. 생산 UDF는 face UDM을
쓰지 않는다. \(B\)는 `udfs/260822_RO_UDF.c`의 `B_perm` (\(2.50\times10^{-8}\)).
\(c_b\)는 이미 있는 `c_b_window_mol_m3`다. 같은 이름의 열을 다시 쓰지 않는다.

\(Q_p\)는 window face 전체를 \(c_m\)으로 정렬한 뒤, 누적 면적 분율이 \(p\)
이상이 되는 가장 작은 \(c_m\)이다. cell 분위를 평균하지 않는다. cell 열
(생산 window는 5–8)은 그 cell의 mid-plane \(c_b\)로 같은 식을 다시 계산한
진단이다.

`raw_report_values.json`의 `derived_values.concentration_cp`에 window와
cell dict가 있다. \(J_w>0\)인 면의 \(\sum J_w A\le 0\)이거나
\(c_b-c_{p,\mathrm{ref}}\le 0\)이면 extract가 실패한다.

이미 추출된 leaf에는 이 열이 없다. 다시 뽑을 때는
`scripts/mfbo/reextract_runs.py --copy-from-production PROD_LEAF --data-root ROOT`
로 복사본만 연다.

## 7. RANS 진단 열

`viscous_model`이 없는 옛 manifest는
`turbulence_metrics_status=legacy_manifest_no_viscous_model`이고 숫자 열은
null이다. `laminar`도 null이다. 그 외에는 fluid zone에서
`viscosity-ratio`, `diff-nacl`, `diffl-nacl`의 volume max/average
(diffl은 min/max)를 읽고, `diffl_*`가 `run_config.mass_diffusivity`
(\(2.0\times10^{-9}\,\mathrm{m^2/s}\))와 상대 1e-9 안에서 같아야
`diff_ratio_* = diff_nacl_* / mass_diffusivity`를 쓴다. 필드가 없거나
diffl이 어긋나면 extract가 실패한다. load-bearing 열에 넣지 않는다.
기본 솔버는 여전히 laminar다.
