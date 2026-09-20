# Figure pipeline — PyEnSight가 실제로 만드는 것

이 문서는 `scripts/pyensight_contour_export.py`와
`scripts/pyensight_extra_figures.py`가 **지금 코드에서** 만들 수 있는
그림의 source of truth다. 측정된 wall time은 저장소에 없다. Fluent/EnSight를
여기서 돌리지 않았다. 식별자·경로·단위는 번역하지 않는다.

`--no-run-pyensight-contours`는 `scripts/batch_postprocess_all_cases.py`의
PyEnSight contour stage만 끈다. extract CSV와는 별개다.
`pyensight_extra_figures.py`는 그 batch에 **연결되어 있지 않다.** 단독
실행이다.

관련 세 번째 스크립트 `scripts/pyfluent_shear_contour_export.py`는 batch
`--run-shear`가 호출한다. 아래 §3에서만, CFF 경로를 리뷰 노트와 맞추기
위해 다룬다.

---

## 1. Inventory

출력은 모두 **PNG**. TIFF/VTK/동영상 없음.

### `scripts/pyensight_contour_export.py`

선택: `--fields` 콤마 목록. 생략 시 기본
`cp_inlet,water_flux,lmh,salt_flux` (코드 `DEFAULT_FIELDS`).
`shear_rate` / `wall_shear_rate` / `velocity_midplane`는 기본에 없다.

한 case 출력 디렉터리:

`{run_leaf}/post/figures/contours/`

파일 이름: `{geo_name}_{case_name}_{output_suffix}.png`.

| `--fields` 키 | 그리는 것 | 표면 | `output_suffix` | 기본 colorbar |
|---|---|---|---|---|
| `cp_inlet` | campaign canonical CP = `udm-9` × per-cell `k_N(x)` (`CP_WALL_CANON`). evaluation window areaavg가 같은 run CSV의 `cp_canon_window_avg`와 안 맞으면 PNG를 안 씀 | 막 (`--membrane-surface` top/bottom/both, 기본 **top**; 검사는 양쪽 막) | `cp_inlet_membrane` | 1.00–1.15 |
| `water_flux` | Jw [m/s]. 가능하면 EnSight 염+압력으로 재구성 (`WATER_FLUX_WALL_DIRECT`), 실패 시 `udm-6` | 막 | `water_flux_membrane` | auto |
| `lmh` | Jw × 3.6e6. 재구성 `LMH_WALL_DIRECT`, 실패 시 `udm-8` | 막 | `lmh_membrane` | 20–30 |
| `salt_flux` | Js [kg/m²/s]. 재구성 `SALT_FLUX_WALL_DIRECT`, 실패 시 `udm-10` | 막 | `salt_flux_membrane` | auto |
| `shear_rate` | `wall-shear / mu` (`SHEAR_RATE_WALL_DIRECT`) | 막만 | `shear_rate_membrane` | auto |
| `wall_shear_rate` | 같은 τ/μ, 막+spacer | `membrane_and_spacer` | `wall_shear_rate_membrane` | auto |
| `velocity_midplane` | velocity **magnitude** [m/s] | 이름에 midplane이 있는 part. 없으면 **3D volume** (WARN) | `velocity_midplane` | 0.0–0.7 |

같이 쓰는 비-그림 파일: `contour_export_status.json`,
`contour_colorbar_ranges.json` / `.txt` / `.csv`
(`--legend-mode hide`가 기본). `--bounds-diagnostics`이면
`pyensight_bounds_diagnostics.json`. `--bounds-debug-sweep`이면 같은 장의
진단 PNG가 더 생긴다 (본 그림을 대체하지 않음).

막 쪽: `--membrane-surface top|bottom|both`. both여도 PNG는 **한 장**이다.

`--manual-view-bounds`를 생략하면 mesh manifest의 측정 길이로 잡는다:
`x = [0, domain_extent_x_m]`, `y`는 origin-centred
`domain_extent_y_m` (없으면 `periodic_shift_y_m`). 10-cell 숫자
`0.010395` 기본값은 없다. zoom이 필요할 때만 flag로 덮어쓴다.

**대략 비용 (구조, 미측정):** EnSight `LocalLauncher` **1회** + field당
PNG 1장. 기본 4장, 전 field면 7장. 그림 수는 `n_active`와 무관하다.
27-cell은 cas/dat가 커서 load·렌더가 길다. 저장소에 측정 wall time은 없다.

### `scripts/pyensight_extra_figures.py`

CLI field 스위치는 없다. `CONFIG` dict와
`PYFLUENT_EXTRA_FIGURES_OVERRIDES` JSON, 그리고 `--run` / `--dry-run`
(기본은 **dry_run=True**, 계획만). `--run`이 있어야 EnSight가 뜬다.

기본 `presentation_contour_mode=True`이므로 실제 그림은:

`{run_leaf}/post/figures/extra/presentation_slices/`
`{run_leaf}/post/figures/extra/presentation_vortex/`
`{run_leaf}/post/figures/extra/presentation_colorbars/`

`presentation_contour_mode=False`이면 classic
`extra/slices/`, `extra/vortex/` (파일명 규칙은 같다). 모듈 docstring의
`03_Results/...`는 **낡았다.** `build_paths`는 run leaf를 쓴다.

기본 `slice_x_fractions = [0.05, 0.25, 0.50, 0.75, 0.95]` (active 막 x-range
분율). 끄고 켜는 키:

| CONFIG 키 | 기본 | 그림 |
|---|---|---|
| `include_concentration` | True | yz: `yz_active_x{fff}_concentration.png` |
| `include_velocity_magnitude` | True | yz: `yz_active_x{fff}_velocity_mag.png` |
| `include_x_velocity` | True | yz: `yz_active_x{fff}_x_velocity.png` (부호 있음, 고정 range 없음) |
| `include_vorticity` | True | yz: `yz_active_x{fff}_vorticity_mag.png` (**데이터셋 vorticity가 있을 때만**; Calculator fallback은 yz에 안 씀) |
| `include_qcriterion` | True | `qcriterion_yz_active_x050.png`; iso `qcriterion_iso_velocity_colored_thr_{tag}.png` + 첫 성공본 복사 `qcriterion_iso_velocity_colored.png` |
| (vorticity mid) | include_vorticity | `vorticity_mag_active_mid.png` (active x clip, z-mid; 여기만 Calculator `Vort` 허용) |

Q iso 기본: 표면은 Q level set, **색은 velocity magnitude**
(`qiso_color_by_velocity=True`). 임계값 우선순위:
`qcriterion_threshold_list` > `qcriterion_threshold` >
`auto_active_max_fraction` (0.02 × active-clip Q max). auto max 읽기
실패 시 sweep
`[100, 300, 1000, 3000, 10000, 30000, 100000]` 1/s².

presentation colorbar (Pillow 필요, 없으면 WARN 후 colorbar만 skip):
`colorbar_concentration.png`, `colorbar_velocity_magnitude.png`,
`colorbar_vorticity_magnitude.png`, `colorbar_qiso_velocity.png`.

농도 후보 목록 **마지막**에 `udm-9` / `cp`가 있다. nacl을 못 찾으면
CP를 concentration 파일명으로 그릴 수 있다.

**대략 비용:** EnSight **1회**. 기본이면 yz 최대 5×4=20장 + Q yz 1 +
vorticity mid 1 + Q iso 1(또는 sweep 7) + colorbar 3–4.
Q/`Vort` Calculator는 active volume clip 위에서 돈다. 그림 수는 역시
`n_active`와 무관하고, 27-cell clip이 더 비싸다.

batch는 이 스크립트를 호출하지 않는다.

---

## 2. 논문 네 장면에 대해 — 있는 것 / 없는 것

존재하지 않는 능력은 만들지 않는다.

### CP on the membrane surface

**있다.** `pyensight_contour_export.py --fields cp_inlet`.

색은 `udm-9 * k_window`이지, CSV의 per-cell `k_N`이 아니다. 기본은
**top 막만**, evaluation window로 clip하지 않는다. colorbar 기본
1.00–1.15. extra_figures는 막 CP가 없다 (yz 농도 후보의 last-resort
`udm-9`는 막 표면이 아니다).

### wall shear rate on the membrane

**있다, 두 경로.**

1. `pyensight_contour_export.py --fields shear_rate` — EnSight,
   `wall-shear / post_config.mu`. 기본 field 아님.
2. `pyfluent_shear_contour_export.py` (batch `--run-shear`) — Fluent CFF
   `wall_shear / mu` 또는 matplotlib fallback. **live Fluent가 필요.**

`wall_shear_rate` 키는 막+spacer다. extra_figures에 shear 없음.
extract의 `wall_shear_rate_*`는 숫자이지 그림이 아니다.

### velocity in the channel, including reverse flow

**부분만 있다.**

- 채널 속 magnitude: extra yz `include_velocity_magnitude=True` (기본).
  contour `velocity_midplane`은 magnitude이고, named mid-plane이 없으면
  3D volume일 수 있다. reverse의 **부호는 안 보인다.**
- reverse flow: extra **`include_x_velocity=True`** yz slices만 부호 있는
  `x-velocity`다. 고정 palette가 없어서 case마다 스케일이 달라질 수
  있다. streamwise-velocity mid-plane field는 **없다.** streamline
  스크립트는 이 두 파일에 없다.

### salt concentration field

**채널 단면만 있다.** extra `include_concentration=True` yz (보통 nacl
mass fraction, presentation 고정 [0.035, 0.045]).

막 표면 염 농도 contour는 **없다.** contour의 `salt_flux`는 Js이지 Yi가
아니다.

---

## 3. Physics-consistency

단일 권위 소스는 없다. 재구성 상수 `A,B,κ,p_perm,MW,ρ,c_0`는 **case-local
`*_RO_UDF.c`**에서 읽고 (`ro.udf_constants`), overlapping subset
(`rho`, `salt_permeability_m_per_s`, `c_inlet_ref`,
`salt_molecular_weight_kg_per_mol`)은 `post_config`와 불일치하면 render를
거절한다. run manifest는 이 상수들을 들고 있지 않다.

### `cp_inlet`

**per-cell `k_N` (option a).** EnSight calculator가
`IfThenElse(AND(GE(X,…),LT(X,…)), k_N, …)`로 구간별 곱을 만든다. 입력은
같은 run의 `summary_metrics_wide.csv` (`pp_cp_canon_rescale_k_cell_N`,
경계 `pp_unit_cell_boundary_i_x_m`, `cp_canon_window_avg`). active cell
키가 evaluation window와 안 맞거나 부분 집합이면 실패한다.

표시는 `--membrane-surface`를 따르지만, CSV 일치는 **양쪽 막** evaluation
window의 `AreaInt` 평균 vs `cp_canon_window_avg` (`rel_tol=5e-3`)다.
EnSight가 그 평균을 돌려주지 못하거나 표와 다르면 PNG를 쓰지 않는다.

### `water_flux` / `lmh` / `salt_flux`

식은 그대로 solution-diffusion 재구성이다. 상수는 이제 case-local UDF다.
EnSight 보간 Yi/p vs UDF face `analytic_cwall`은 남을 수 있다. 재구성
실패 시 UDM fallback + WARN.

### shear

PyEnSight `shear_rate`는 여전히 `τ/cfg.mu`. CFF silent skip은
`pyfluent_shear_contour_export.py`만.

---

## Extra figures — reconstruction? (고치지 않음)

`pyensight_extra_figures.py`는 UDF 상수로 물리량을 다시 만들지 않는다.

- `concentration` yz: EnSight 변수 후보를 순서대로 읽는다. 앞쪽이 nacl /
  mass-fraction / yi. **last-resort에 `udm-9`가 있다.** nacl이 있으면
  직접 읽기이고, 없으면 CP를 concentration 파일명으로 그릴 수 있다.
- `velocity_mag`: velocity-magnitude 직접 읽기.
- `x_velocity`: x-velocity 직접 읽기 (부호 있음). reverse flow에 쓰는 장.
- `vorticity_mag` yz: 데이터셋 vorticity만. 없으면 그 슬라이스는 skip.
- Q iso / vorticity mid: 데이터셋에 없으면 EnSight Calculator가 velocity
  벡터에서 `Q_criteria` / `Vort`를 만든다. 그건 kinematic derived field이지
  UDF 재구성이 아니다.

---

## 4. Staleness — `c_b` CSV then JSON

`_read_pyfluent_c_b_window_mol_m3`는 여전히
`summary_metrics_wide.csv`의 `c_b_window_mol_m3`를 먼저 읽고, 없거나
비면 `raw_report_values.json` →
`derived_values.segmented_cp_values.c_b_window_mol_m3`다. timestamp /
sha256 / extract identity 비교는 **없다.**

**live CP contour는 이 함수를 안 부른다.** `k_N`은 같은 CSV에서 읽는다.
CSV↔JSON 혼선은 현재 CP PNG 경로의 직접 원인이 아니다.

p6M extract가 한 leaf에서 `csv_write`로 CSV를 쓰면, 그 leaf의
`summary_metrics_wide.csv`만 가지고 CP 그림을 돌린다. 부분 키는 이제
거절한다.

---

## 5. Cost and scope

저장소에 per-case wall time이 없다. 아래는 구조다.

| 세트 | EnSight | Fluent solver | PNG 수 (기본) | 7-active vs 27-active |
|---|---|---|---|---|
| contour 기본 4 field | 1 session | 없음 | 4 | 같은 장 수; 27-cell load가 큼 |
| contour 전 field | 1 | 없음 | 7 | 동일 |
| extra 기본 presentation | 1 | 없음 | ~22–30 + colorbar | 동일 장 수; Q/clip이 27-cell에서 큼 |
| shear CFF (`pyfluent_shear_contour_export.py`) | 없음 | **필요** | 막 side PNG | Fluent 그래픽 세션 |

코드에서 읽을 수 있는 것은 PNG 수와 EnSight 1회이지, 분 단위 wall time이
아니다. 27-cell cas/dat와 Q clip이 더 크다.

두 PyEnSight 스크립트는 `{geo}_{case}_final.cas.h5` + `_final.dat.h5`만
로드한다 (`LocalLauncher`). extract가 붙잡고 있는 Fluent session은
쓰지 않는다. `--run-shear`는 Fluent가 필요하므로 extract와 같은 Fluent
라이선스를 다툴 수 있다.

extra는 기본 dry-run이다. 실파일은 `--run`이 필요하다.

---

## 논문용 한 줄

막 CP `--fields cp_inlet`은 per-cell `k_N`이고, evaluation window
areaavg가 `cp_canon_window_avg`와 안 맞으면 렌더를 거절한다. 막 shear는
PyEnSight `--fields shear_rate` 또는 Fluent CFF. reverse flow는 extra yz
`x_velocity`. 염 막 contour는 없다. extra yz 농도와 속도는 직접 읽기다.
