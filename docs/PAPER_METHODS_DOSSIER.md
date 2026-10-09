# Paper methods dossier

Source of truth for a Methods section of the benchmark paper (Diamond, ML, Pillar, Sinusoidal, plus the Empty reference). Every value below is taken from this repository. A cell marked **OPEN** is a placeholder, an unresolved modelling comment, or a choice this repository does not make. Do not fill an OPEN cell from a typical Fluent default.

Paper decisions already fixed, and where the code records them:

| Decision | Value used here | Source |
| --- | --- | --- |
| Flow regime | laminar | `configs/run_config.py:291` |
| Concentration polarisation | concentration-statistics average and `CP_q999` | `src/ro/cp_concentration_stats.py:201–234` |
| LMH | window module area `lmh_window_module` is the paper and MFBO value; window exposed area is reported alongside; `lmh_mass_balance` is the gate only | `src/ro/evaluation_window_table.py` |
| Convergence gate | post-hoc gate, independent of the solver stop | `src/ro/convergence_quality.py:1–32` |
| Production UDF | `udfs/260822_RO_UDF.c` | `configs/run_config.py:243` |
| Mesh used in the paper | **OPEN** | user decision; production mesh id is recorded below and is not the paper mesh |
| Diamond evaluation window | per-geometry `configs/evaluation_window_table.json` (2026-10-09). The fixed lead of 3 is superseded; mesh manifests still store it | `scripts/mfbo/development_length.py` |

The 2D pilot UDF `udfs/260929_RO_UDF.c` is not the 3D campaign UDF.

---

## 1. Geometry

Lengths in the geometry registry are metres. The meshing periodic shift is millimetres.

### 1.1 Channel and contact, all families

| Setting | Value | Unit | Source |
| --- | --- | --- | --- |
| Channel height `h` | 0.00077 | m | `src/ro/campaign_geometry.py:15` |
| Diamond filament diameter | 4.0e-4 | m | `src/ro/campaign_geometry.py:17` |
| Stacked outer height `Sigma_d` (Diamond, ML, Sinusoidal) | 0.00080 | m | `src/ro/campaign_geometry.py:23` |
| Membrane penetration `membrane_trim_m` | `(Sigma_d - h) / 2` = 1.500000000000004e-05 as evaluated | m | `src/ro/campaign_geometry.py:21–24` |
| Pillar and Empty penetration | 0 | m | `src/ro/geometry_registry.py:134`; `src/ro/campaign_geometry.py:434` |
| Contact-band width | `w = 2 * sqrt(d * t - t**2)`, `t = membrane_trim_m` | m | `src/ro/campaign_geometry.py:189–196` |
| LMH blocked-area fraction (consumed) | 0.0 for every family | — | `src/ro/campaign_geometry.py:86` |
| Porosity | **OPEN** (registry stores `None`; measured at mesh time from fluid volume / bounding box) | — | `src/ro/campaign_geometry.py:298–299` |

Evaluated contact widths (return values of `membrane_contact_width_m`, not stored literals):

| Family | Contact diameter `d` | `w` (m) |
| --- | --- | --- |
| Diamond | 4.0e-4 m | 0.00015198684153570682 |
| Sinusoidal (wave diameter `2 * 4.0e-4`) | 8.0e-4 m | 0.00021702534414210734 |
| ML `M_c160` (outer layer) | 0.000320 m | 0.00013527749258468701 |
| ML `M_c267` (outer layer) | 0.000266670 m | 0.00012288287106020936 |
| ML `M_c400` (outer layer) | 0.000200 m | 0.00010535653752852752 |

ML uses the outer-layer diameter for the contact width, not the middle-layer diameter stored as `filament_d_m` (`src/ro/campaign_geometry.py:321–335`). Geometric blockage is not the LMH denominator. Diamond and Sinusoidal store `membrane_blocked_area_frac_geometric = None` (**OPEN** as a number). Pillar geometric fractions are in §1.5 and are unused by LMH.

### 1.2 Domain layout, buffers, periodicity

Current generation, written into mesh manifests from `configs/batch_config.py:141–158`:

| Setting | Value | Unit | Source |
| --- | --- | --- | --- |
| Zone counts | 1 buffer in + `n_active` + 2 buffer out | — | `src/ro/domain_layout.py:307–314` |
| Inlet buffer length | 0.003465 | m | `src/ro/domain_layout.py:54` |
| Outlet buffer length | 0.00693 | m | `src/ro/domain_layout.py:55` |
| Domain origin `domain_x_min_m` | 0.0 | m | `configs/run_config.py:352` |
| Total length | `buffer_in + n_active * pitch + buffer_out` | m | `src/ro/domain_layout.py:10–11` |
| Membrane walls | `wall_top_mem`, `wall_bottom_mem` (active span only) | — | `src/ro/domain_layout.py:57` |
| Buffer walls (current CAD) | `wall_top_buffer_in`, `wall_top_buffer_out`, `wall_bottom_buffer_in`, `wall_bottom_buffer_out` | — | `src/ro/domain_layout.py:59–64` |
| Periodic labels | `periodic_l`, `periodic_r`; reference `periodic_r` | — | `configs/run_config.py:121–122` |
| Periodic translation | x = 0, z = 0; y from the geometry (mm in meshing) | mm | `configs/run_config.py:125–127`; `src/ro/campaign_matrix.py:118–119` |
| Periodic setup order | surface mesh first, then periodic boundaries (`periodic_after_surface_mesh = True`) | — | `configs/run_config.py:214` |

`configs/run_config.py:353–354` (`domain_length_m = 0.017325`, `buffer_length_m = 0.003465`) is the legacy symmetric triple. `src/ro/domain_layout.py:13–16` says that triple cannot express 1+7+2. Do not use 0.017325 m as the campaign domain length. Stock `configs/post_config.py:112–117` leaves the layout keys `None` and the extract refuses to guess.

`overlap_m` is 0.0 on every campaign entry (`src/ro/campaign_geometry.py:287`).

### 1.3 Diamond

`joint_sphere_count` is 0 and the `joint_sphere_*` fields are `None` (`src/ro/campaign_geometry.py:302–309`). The CAD sphere at the 90° crossing is `bridge_radius_m = 1.10e-4` m (`src/ro/campaign_geometry.py:18`, `:286`). The comment at `src/ro/campaign_geometry.py:135–137` records that radius as the Diamond sphere (equal 0.200 mm radii, gap `g = 30` µm) and says to leave it.

`(n_active, pitch_mm, periodic_dy_mm, attack_angle_deg)` from `src/ro/campaign_geometry.py:67–77`:

| geo_id | n_active | pitch (mm) | periodic dy (mm) | angle (deg) |
| --- | --- | --- | --- | --- |
| D2450_a45 | 7 | 3.465 | 3.465 | 45 |
| D0817_a45 | 21 | 1.155 | 1.155 | 45 |
| D2450_a60 | 5 | 4.900249992 | 2.8291606522 | 60 |
| D2450_a30 | 9 | 2.8291606522 | 4.900249992 | 30 |
| D1225_a30 | 18 | 1.4145803261 | 2.450124996 | 30 |
| D1225_a45 | 14 | 1.7325 | 1.7325 | 45 |
| D1225_a60 | 10 | 2.450124996 | 1.4145803261 | 60 |
| D0817_a30 | 27 | 0.9430535507 | 1.633416664 | 30 |
| D0817_a60 | 15 | 1.633416664 | 0.9430535507 | 60 |

Pitch and periodic dy are converted to metres by multiplying by `1.0e-3` (`src/ro/campaign_geometry.py:279–280`). `periodic_shift_y_source` is `derived_from_angle`.

### 1.4 ML

Common: periodic shift 0.003465 m, 7 active cells, cell length 0.003465 m, two joint spheres per node, attack angle stored as 0 (`src/ro/campaign_geometry.py:79–95`, `:325`). Layer angles +45° / 90° / −45° (`src/ro/campaign_geometry.py:79`). Axes and sphere centres: mid-plane at z = 0; outer axes at ±(r_mid + r_outer); spheres at ±r_mid (`src/ro/campaign_geometry.py:258–268`).

| geo_id | layer diameters top/mid/bot (m) | sphere R (m) | r_min (m) | Source |
| --- | --- | --- | --- | --- |
| M_c160 | 0.000320 / 0.000160 / 0.000320 | 0.000100 | 0.000080 | `src/ro/campaign_geometry.py:139–144` |
| M_c267 | 0.000266670 / 0.000266670 / 0.000266670 | 0.000105 | 0.000133335 | `:145–150` |
| M_c400 | 0.000200 / 0.000400 / 0.000200 | 0.000110 | 0.000100 | `:151–156` |

`joint_sphere_R_ratio` is `R / r_min` in the same table. `membrane_blocked_area_frac_geometric` is `None` (**OPEN**).

### 1.5 Pillar

Unit cell 0.003465 m × 0.003465 m (`src/ro/campaign_geometry.py:161`). Flat ends: trim 0, `bridge_radius_m` 0, `membrane_contact_width_m` `None` (`src/ro/geometry_registry.py:129–135`). Attack angle in the registry is 0. Production meshing leaves the Pillar (and ML) attack angle on the common-template value 45° (`src/ro/campaign_matrix.py:149–154`, `configs/batch_config.py:155`). Filament diameter passed in is 4.0e-4 m (`src/ro/campaign_geometry.py:372`).

Bore and pillar diameters are parsed from the geo_id: token hundredths of a millimetre, then × 1e-3 to metres (`src/ro/campaign_geometry.py:355–360`). `h00` forces bore diameter 0 (`:370–371`).

| Token | D_p (mm) | geometric blocked fraction (unused by LMH) | Source |
| --- | --- | --- | --- |
| P_p60 | 0.60 | 0.047 | `src/ro/campaign_geometry.py:164–167` |
| P_p80 | 0.80 | 0.084 | same |
| P_p100 | 1.00 | 0.131 | same |

Hole flags (`src/ro/campaign_geometry.py:173–183`): `h00` false; `h15` and `h30` true, for D_p = 0.60, 0.80, and 1.00 mm. Bore diameters are 0, 0.15, and 0.30 mm.

`P_p80_h00_f320` and `P_p80_h15_f320` (`d_f = 0.320` mm) have no CAD and were never meshed (`src/ro/campaign_geometry.py:184–186`). They are not campaign geometries.

The geometric blocked fraction in the registry entry is also `pi * (D_p/2)^2 / (L^2/2)` with `L = 0.003465` m (`src/ro/geometry_registry.py:119–120`). The table above is the consumed campaign table; the formula is a second computation in `pillar_registry_entry`. Methods should cite the table if quoting 0.047 / 0.084 / 0.131.

### 1.6 Sinusoidal

Spanwise period `W = 3.465e-3` m (`src/ro/campaign_geometry.py:30`). Wave radius 4.0e-4 m, so the contact diameter is 8.0e-4 m (`:26`, `:385`). No spherical joint: `bridge_radius_m = 0`, `joint_sphere_count = 0` (`:390–414`). Cylindrical spanwise bridges are CAD on `wall_spacer_bridge`, not `bridge_radius_m` (`:390–391`). Cell length and periodic shift are `W`. Seven active cells. Attack angle 0. Same stacked trim as Diamond.

Amplitudes (`:31–35`): `a072 = W/48`, `a144 = W/24`, `a193 = W/18`.

Wavelengths (`:36–39`): `l1733 = W/2`, `l3465 = W`, `l6930 = 2W`.

`curvature_margin = wavelength^2 / (4 * pi^2 * amplitude * wave_radius)` (`src/ro/campaign_geometry.py:250–255`). `membrane_blocked_area_frac_geometric` is `None` (**OPEN**).

### 1.7 Empty reference

`REF_empty`: filament diameter 0, bridge radius 0, trim 0, contact width 0, 7 active cells, cell length 0.003465 m, periodic shift 0.003465 m, `Sigma_d` set equal to the channel height (`src/ro/campaign_geometry.py:423–439`). The comment at `:430` says the legacy 3-cell empty channel is archive-only.

---

## 2. Governing equations and physical properties

This repository does not write the bulk Navier–Stokes or species PDE. The solver asserts that Fluent’s viscous model is already `laminar` and does not set it (`scripts/solver_code_260616.py:151–159`, `configs/run_config.py:286–291`). It prints the species-model and energy-model state and does not assign either (`scripts/solver_code_260616.py:4661–4665`). Whether the energy equation is off is **OPEN** (not determined here).

The mixture mass diffusivity is read back and must be the constant dilute approximation equal to `mass_diffusivity` (`scripts/solver_code_260616.py:814–828`, `:4686–4692`). Mixture density and viscosity are printed from the template (`:4680–4684`) and are not assigned. `mixture_density` is used only in the LMH report expression (`scripts/solver_code_260616.py:4840`). `salt_density`, `salt_viscosity`, and `mixture_viscosity` are bound from the config and not written to Fluent. The numbers below are the campaign constants. Agreement of the template density and viscosity with those constants is **not checked**.

| Property | Value | Unit | Source |
| --- | --- | --- | --- |
| Viscous model | laminar | — | `configs/run_config.py:291` |
| Species name | `nacl` (material name `salt`) | — | `configs/run_config.py:268–270` |
| Inlet salt mass fraction | 0.035 | — | `configs/run_config.py:271` |
| Mixture / salt density (config) | 998.2 | kg/m³ | `configs/run_config.py:273`, `:278` |
| UDF reference density `RHO_REF` | 998.20 | kg/m³ | `udfs/260822_RO_UDF.c:49` |
| Post-process density | 998.2 | kg/m³ | `configs/post_config.py:72` |
| Mixture / salt viscosity (config) | 8.93e-4 | Pa·s | `configs/run_config.py:274`, `:279` |
| Post-process viscosity | 8.93e-4 | Pa·s | `configs/post_config.py:73` |
| Mass diffusivity | 2.0e-9 | m²/s | `configs/run_config.py:280` |
| UDF `D_SALT` | 2.0e-9 | m²/s | `udfs/260822_RO_UDF.c:94` |
| Diffusivity option required on readback | `constant-dilute-appx` | — | `scripts/solver_code_260616.py:824–828` |
| Molecular weight in UDF `MW_SALT` | 0.05844 | kg/mol | `udfs/260822_RO_UDF.c:47` |
| Molecular weight in `run_config` | 58.44 | as stored (g/mol-style number; the solver does not write it) | `configs/run_config.py:275` |
| Molecular weight in post_config | 0.05844 | kg/mol | `configs/post_config.py:75` |
| Inlet / reference concentration | 597.8268309 | mol/m³ | `udfs/260822_RO_UDF.c:48`; `configs/post_config.py:74` |
| Water permeability `A` | 2.50e-12 | m/(s·Pa) | `udfs/260822_RO_UDF.c:193` |
| Salt permeability `B` | 2.50e-8 | m/s | `udfs/260822_RO_UDF.c:194`; `configs/post_config.py:76` |
| Osmotic coefficient `kappa` | 4958.0 | Pa·m³/mol | `udfs/260822_RO_UDF.c:195` |
| Permeate pressure `p_perm` | 101325.0 | Pa | `udfs/260822_RO_UDF.c:196` |
| Fluent operating pressure | 101325.0 | Pa | `configs/run_config.py:232`; written at `scripts/solver_code_260616.py:4708` |
| Outlet gauge pressure | production matrix below | Pa | not a single constant (`configs/run_config.py:233`) |
| Inlet speed | production matrix below | m/s | not a single constant (`configs/run_config.py:229`) |

Molar concentration in the UDF is `cm = RHO_REF * Y_salt / MW_SALT`, with `Y_salt` clamped to [0, 1] (`udfs/260822_RO_UDF.c:620–628`). Absolute pressure is cell-centre gauge pressure plus Fluent `operating-pressure` (`udfs/260822_RO_UDF.c:551`, `:617–618`):

```
p_abs = C_P + p_op
dp    = p_abs - p_perm
```

With both `p_op` and `p_perm` equal to 101325 Pa, `dp` equals the gauge pressure. The driving pressure of a case is `outlet_gauge_pressure`, not `operating_pressure`.

### 2.1 Operating points

Production sweep, nine points per geometry (`src/ro/campaign_matrix.py:52–53`, `:168–171`):

| Inlet velocity | Outlet gauge pressure |
| --- | --- |
| 0.1, 0.2, 0.3 m/s | 4.0e6, 6.0e6, 8.0e6 Pa |

Which of these nine the paper tables report is **OPEN**. The repository defines the full matrix.

### 2.2 Inlet velocity profile

Production cases merge `common_solver_settings`, which sets `use_inlet_velocity_profile = True` (`configs/batch_config.py:421–430`, `scripts/batch_solver_sweep.py:522`). The module default in `configs/run_config.py:249` is `False` (plug inlet through `velocity_magnitude`). The comment at `configs/run_config.py:247–248` says the `True` path (velocity components plus the UDF) needs live verification on Fluent 25.1. That verification is **OPEN**.

When the flag is true, the profile is plane Poiseuille in z only (`udfs/260822_RO_UDF.c:1053–1064`, `:1143–1167`):

```
eta   = (z - INLET_Z_BOTTOM) / CHANNEL_HEIGHT
shape = 6 * eta * (1 - eta)
G     = sum(shape_i * dA_i) / sum(dA_i)
u_i   = U_TARGET * shape_i / G
```

| Setting | Value | Unit | Source |
| --- | --- | --- | --- |
| `INLET_Z_BOTTOM` | −0.385e-3 | m | `udfs/260822_RO_UDF.c:1090` |
| `CHANNEL_HEIGHT` | 0.770e-3 | m | `udfs/260822_RO_UDF.c:1092` |
| `U_TARGET` in the master file | 0.2 (placeholder) | m/s | `udfs/260822_RO_UDF.c:1093–1097` |
| `U_TARGET` on a case | patched from `inlet_velocity_value` | m/s | `scripts/solver_code_260616.py:1116`, `:1146` |

`eta` is clamped to [0, 1]. The discrete area average equals `U_TARGET`. Centreline speed is `1.5 * U_TARGET / G`.

---

## 3. Membrane boundary model (UDF)

Production compile flag `RO_ANALYTIC_CWALL = 1` (`udfs/260822_RO_UDF.c:157–158`). The header at `:145–155` says the 1D reconstruction ignores tangential convection and that whether it is the right closure this close to the wall is unresolved. The implemented equation is the one below. That closure question is **OPEN**. The same header says the production decision remains the reconstruction.

Cell-centre quantities are used first (`udfs/260822_RO_UDF.c:612–616`). `y1` is the normal distance from the wall-face centroid to the adjacent cell centroid (`:631–639`).

### 3.1 Water flux

Quadratic solution-diffusion (`udfs/260822_RO_UDF.c:650–659`), then one Picard update after reconstruction (`:683–688`):

```
S     = A * (dp - kappa * cm)
disc  = (S + B)^2 + 4 * A * B * kappa * cm
Jw    = 0.5 * (S - B + sqrt(max(disc, 0)))
Jw    = max(Jw, 0)
```

### 3.2 Permeate concentration

```
cp = B * cm * Jw / (Jw + B)     when Jw + B > 1.0e-20
cp = 0                          otherwise
Js = max(cp, 0)                 (molar salt flux; cp here is Js)
```

(`udfs/260822_RO_UDF.c:661–668`, `:689–696`). The stored diagnostic uses the same quotient as `cp_perm = B * cm / (Jw + B)` (`:369–384`).

### 3.3 Wall-concentration reconstruction

```
cp    = B * c_1 / (Jw + B)      (same threshold)
c_w   = cp + (c_1 - cp) * exp(Jw * y1 / D_SALT)
c_w   = max(c_w, 0)
```

(`udfs/260822_RO_UDF.c:400–425`). `c_1` is the adjacent-cell concentration. The quadratic is solved again with `cm` replaced by `c_w` (`:678–697`).

### 3.4 Diagnostic per-face CP (not a source)

```
CP = (cm - cp) / (C_INLET_REF - cp)
```

with unramped `Jw` (`udfs/260822_RO_UDF.c:367–396`). If the denominator is not positive the UDF returns 0 and sets a failure flag. This is the singular per-face form in §5.5. It is written to a UDM slot and does not feed the source terms (`docs/metrics_conventions.md:264–271`).

### 3.5 Source terms

Ramp multiplies the fluxes only (`udfs/260822_RO_UDF.c:728–741`):

```
Jw_src = Jw_phys * ramp
Js_src = Js_phys * ramp
Sm     = Jw_src * dA * RHO_REF / dV      water mass sink [kg/m³/s]
Si     = (Js_src * MW_SALT) * dA / dV    salt mass sink [kg/m³/s]
Stot   = Sm + Si
```

`DEFINE_SOURCE` returns `-Stot` (mixture mass), `-Si` (salt), and `-m_i * velocity_i` (momentum), with `dS[eqn] = 0` for mass and species and `dS[eqn] = -m_i` for momentum (`udfs/260822_RO_UDF.c:976–1013`). `USE_NORMAL_PROJECTED_MOMENTUM_SINK` is 0, so the momentum coefficient is the isotropic total mass sink (`:81`, `:762–769`). Sources are zero until enough UDM slots exist (`:264–270`, `:980–982`).

### 3.6 Ramping

UDF source ramp, by iteration count `N_ITER` (`udfs/260822_RO_UDF.c:59–66`, `:274–282`):

| Iteration | Factor |
| --- | --- |
| `< 50` | 0.2 |
| `< 100` | 0.5 |
| `< 150` | 0.8 |
| otherwise | 1.0 |

Diagnostics use the unramped fluxes (`udfs/260822_RO_UDF.c:699`).

A separate solver-side safety is on: `use_ramp_convergence_safety = True`, `ramp_full_iteration = 150`, `post_ramp_buffer_iterations = 50` (`configs/run_config.py:312–314`). QoI stopping ignores the first 200 iterations (`configs/run_config.py:335`), which is the 150-iteration ramp plus that 50-iteration buffer.

---

## 4. Solver settings

Product version string `25.1.0`, 50 processors (`configs/run_config.py:94–95`). Those are launch settings, not discretisation.

| Setting | What the production path does | Source |
| --- | --- | --- |
| Pressure–velocity coupling | **OPEN.** Not written. `pseudo_time_time_step_size_scale_factor = "preserve"` returns without asserting a scheme. A numeric scale factor would require readback `flow_scheme == "Coupled"` and global time step; production does not request one. | `configs/run_config.py:302–304`; `scripts/solver_code_260616.py:3761–3764` |
| Spatial discretisation | **OPEN** for laminar. Second-order upwind is forced only on turbulence equations, and only when the viscous model is not laminar. | `scripts/solver_code_260616.py:188–204`; `configs/run_config.py:286–291` |
| 1st-to-higher-order blending | `"preserve"` (template unchanged) | `configs/run_config.py:305–308` |
| Under-relaxation | **OPEN** as numbers. Profile `"baseline"` leaves the controls unchanged. Named profiles that are not production: conservative (pressure 0.2, momentum 0.3, species 0.5) and strong (0.1, 0.2, 0.3). | `configs/run_config.py:295`; `scripts/solver_code_260616.py:4098–4102`, `:3373–3383` |
| Species implicit URF | `"preserve"` | `configs/run_config.py:297–298` |
| Pseudo-time verbosity | `"preserve"` | `configs/run_config.py:299–301` |
| Initialisation | Fresh runs: hybrid, then patch `species-<salt index>` on fluid zones to `salt_mass_fraction` (0.035). Restarts skip both. | `scripts/solver_code_260616.py:5093–5122`; `configs/run_config.py:271` |
| Iteration limit | 2000 | `configs/run_config.py:293`; `configs/batch_config.py:423` |
| Residual criterion | 1e-7 on continuity, x/y/z-velocity, and `nacl`, written to `absolute_criteria` or `relative_criteria` according to which field the template exposes | `configs/run_config.py:292`; `scripts/solver_code_260616.py:2939–2946`, `:3012–3023` |
| Residual check | `check_convergence = True` on those equations | `scripts/solver_code_260616.py:3013` |
| QoI stop | enabled; condition `all-conditions-are-met` | `configs/run_config.py:316–327`; `scripts/solver_code_260616.py:3104` |
| QoI reports | `lmh_udm_avg` and `pressure_drop_spacer` | `configs/run_config.py:332`; `scripts/solver_code_260616.py:3105` |
| QoI window | relative, criterion 1e-3, 100 previous values, ignore the first 200 | `configs/run_config.py:320–335` |

The live stop is LMH and spacer pressure drop and the residual checks together (`configs/run_config.py:317–320`). The post-hoc gate in §6 is separate and can fail a run that stopped on this criterion.

Energy on/off, the pressure–velocity scheme, the convective schemes, and the under-relaxation factors are whatever is in `template_RO_setup.cas.h5` (`configs/run_config.py:236`). This repository does not record those template values.

---

## 5. Meshing workflow

The paper mesh is **OPEN**. The rows below are the current production campaign settings, not a mesh-independence choice. `D0817_a60` is the one production exception: `m_max = 0.060` mm, mesh id `max060_min006_cpg5_bl4_peel2`. Every other production geometry uses `m_max = 0.085` mm and `max085_min006_cpg5_bl4_peel2` (`src/ro/campaign_matrix.py:55–59`).

### 5.1 Fixed on the production template

| Parameter | Value | Unit | Source |
| --- | --- | --- | --- |
| `m_min` | 0.006 | mm | `configs/batch_config.py:150` |
| `m_cpg` | 5 | cells per gap | `configs/batch_config.py:151` |
| `bl_layers` | 4 | — | `configs/batch_config.py:152` |
| `spacer_bl_layers` | `None` (one boundary-layer control) | — | `configs/run_config.py:136–138` |
| `peel_layers` | 2 | — | `configs/run_config.py:172` |
| `bl_offset_method` | `smooth-transition` | — | `configs/run_config.py:148` |
| `bl_growth_rate` | 1.2 | — | `configs/run_config.py:149` |
| `bl_height_factor` | 0.4 | — | `configs/run_config.py:134` |
| First height passed to meshing | `m_min * bl_height_factor` | mm | `scripts/meshing_code_260616.py:622–623` |
| `boi_curvature_normal_angle` | 18 | deg | `configs/run_config.py:130` |
| `boi_growth_rate` | 1.2 | — | `configs/run_config.py:131` |
| `vol_hex_max_factor` | 0.7 | — | `configs/run_config.py:152` |
| `include_spacer_in_boundary_layers` | True | — | `configs/run_config.py:142` |
| `periodic_after_surface_mesh` | True | — | `configs/run_config.py:214` |
| Min orthogonal quality gate | 0.05 | — | `configs/run_config.py:175` |
| Max aspect-ratio gate | 150 | — | `configs/run_config.py:182` |
| Max surface skewness gate | 0.85 | — | `configs/run_config.py:192` |
| Skewed-face fraction gate (skewness > 0.80) | 3.0e-5 | — | `configs/run_config.py:204` |

`peel_layers` changes the prism-to-hexcore transition, not the membrane first-cell height (`configs/run_config.py:153–161`). The comment at `configs/run_config.py:166–171` says the measured membrane `y1` on one Diamond mesh was several times the first height implied by `bl_height`, because smooth-transition does not honour `FirstHeight` as a wall distance. Do not quote `bl_height/2` as `y1`.

### 5.2 What varies

| Knob | Where it varies | Source |
| --- | --- | --- |
| `m_max` | 0.085 mm production default; 0.060 mm for `D0817_a60` only | `src/ro/campaign_matrix.py:55–63` |
| `n_active_cells`, pitch, `periodic_shift_y`, attack angle, spacer labels | per geometry, from the registry (Pillar and ML attack angle stay at the template 45°) | `src/ro/campaign_matrix.py:141–154` |
| `m_min`, `m_cpg`, `bl_layers`, `spacer_bl_layers`, `peel_layers` | required case fields; mesh-study leaves change them. Not varied inside the production id above. | `configs/run_config.py:107–109`, `:135` |
| `bl_height_factor` | optional mesh-id token `_fNNN`; absent on the production id | `configs/run_config.py:728–730` |

Mesh-independence reduction (Richardson, GCI, safety factor 1.25) is implemented in `scripts/mfbo/mesh_convergence.py` and described in `docs/MESH_LANDSCAPE.md`. No paper mesh has been selected. **OPEN.**

---

## 6. Post-processing definitions

### 6.1 Evaluation window

**Paper window (2026-10-09).** Extract (`scripts/pyfluent_report_extract.py`, full and `--profile mfbo`) reads `configs/evaluation_window_table.json`. The row for the run's `geo_id` sets `n_lead_excluded`. A missing `geo_id` is an error. `--legacy-3-cell-window` scores lead 3 and records `window_table_version` `legacy-3-cell`; it does not read the table. Trail exclusion is 0. The same cells are the CP window (`cpc_window_avg_flux`, `cp_q999_window_flux`) and the LMH window in §6.3. The run manifest and the wide summary record `n_lead_excluded`, `excluded_length_m`, `window_length_m`, and `window_table_version`.

**Superseded 2026-10-09.** The fixed lead of 3 below is not the paper window. Mesh manifests still store lead 3; extract overwrites that with the table unless the legacy flag is set.

| Setting | Status | Source |
| --- | --- | --- |
| Stock post_config | `n_lead_excluded = None`, `n_trail_excluded = None`. Extract refuses a missing window. | `configs/post_config.py:125–131` |
| Value written into current mesh manifests | lead 3, trail 0 | `configs/batch_config.py:147–148` |
| Named constants | legacy lead 1; `CURRENT_EVALUATION_WINDOW` lead 3, trail 0 | `src/ro/domain_layout.py:322–323` |
| Paper window, including Diamond | **Superseded 2026-10-09** by the table above. This row recorded the window as **OPEN** while manifests wrote lead 3. | user decision, closed 2026-10-09 |

Until 2026-10-09, `src/ro/domain_layout.py` treated lead = 3 as a D2450 cell-count convention and left open whether the exclusion should follow cell count or an absolute length. Evaluation cells are the active cells after dropping `n_lead_excluded` at the inlet end and `n_trail_excluded` at the outlet end (`src/ro/domain_layout.py:267–275`). On 1+7+2 with lead 3 and trail 0 that is global cells 5–8. That fixed window is superseded. `CURRENT_EVALUATION_WINDOW` remains the lead-3 constant used by `--legacy-3-cell-window`. Hardcoded cells `(4, 5, 6, 7)` are a continuity column only (`src/ro/convergence_quality.py:33`, `:9–13`).

### 6.2 Bulk concentration `c_b`

Channel mid-plane `z = 0.5 * (z_min + z_max)` from the fluid bounds (`src/ro/fluent_report_helpers.py:196–201`). Per evaluation cell, an x-range clip of that plane, surface-massavg of salt (`src/ro/fluent_report_helpers.py:1632–1648`). Mass-weighted. Each cell’s ratios use that cell’s `c_b`; the window ratios use the window mid-plane `c_b` (`src/ro/cp_concentration_stats.py:248–251`).

A guard compares each cell’s mid-plane `c_b` with the mean of the mixing-cup concentrations on the two flanking x-normal cell boundaries. Relative tolerance 0.005 (`src/ro/fluent_report_helpers.py:286–299`).

### 6.3 LMH, both bases

**Paper and MFBO LMH (2026-10-09).** Over the evaluation-window cells, from per-cell UDF flux `pp_jw_m_per_s_cell_N` and `pp_membrane_area_cell_N_m2`:

```
lmh_window_exposed = sum(Jw_i * A_mem_i) / sum(A_mem_i) * 3.6e6
lmh_window_module  = sum(Jw_i * A_mem_i) / (2 * L_window * periodic_shift_y_m) * 3.6e6
```

`L_window` is `window_length_m`. `lmh_window_module` is the paper LMH and the MFBO objective (`src/ro/mfbo_adapter.py`). `lmh_window_exposed` is reported alongside. `lmh_mass_balance` is the convergence-gate quantity only.

**Superseded as the objective 2026-10-09.** The whole-active rescale below remains the reference column `lmh_module_area`. It is not the objective.

Exposed-area (mass balance), blocked fraction 0 so the denominator is `area_mem`:

```
lmh_mass_balance = abs(m_in + m_out) / (rho * area_mem * (1 - f_blocked)) * 3.6e6
```

(`src/ro/lmh_metrics.py:36–53`, `:80–87`). `m_in` and `m_out` are mass-flow reports (kg/s). `3.6e6` converts m/s to L/(m²·h). The UDF uses the same factor `3600000` (`udfs/260822_RO_UDF.c:52`).

Module area (`scripts/mfbo/summarize_results.py:98–122`):

```
A_module         = 2 * n_active_cells * cell_length_x_m * periodic_shift_y_m
lmh_module_area  = lmh_mass_balance * area_mem / A_module
```

`area_mem` is the meshed membrane area (both walls). `A_module` is the projected area of both membranes over the active cells, including the contact footprint. `f_blocked` consumed is 0, so the exposed basis is not reduced by the geometric contact fractions in §1.5.

`lmh_udm_avg` is a separate surface average of the UDF LMH UDM, used as a solve-time monitor and in the gate of §7. It is not the reported LMH.

### 6.4 Pressure drop per length

Solve-time and extract planes are the active-span ends (`scripts/solver_code_260616.py:2782–2827`):

```
pressure_drop_spacer     = areaavg(p at x_in) - areaavg(p at x_out)     [Pa]
pressure_drop_spacer_per_m = pressure_drop_spacer / active_length        [Pa/m]
```

`active_length = n_active * cell_length_x` (`src/ro/fluent_report_helpers.py:461–469`; divide at `scripts/pyfluent_report_extract.py:2241–2243`). This is the spacer span, not the full inlet-to-outlet domain. `pressure_drop_per_m` (full domain) is a different column (`scripts/pyfluent_report_extract.py:2233`).

### 6.5 Concentration-statistics CP (paper definition)

Per face, `cp = B * cm / (Jw + B)` (`src/ro/cp_concentration_stats.py:117–129`). `B` is parsed from `udfs/260822_RO_UDF.c:194`.

Reference permeate (`src/ro/cp_concentration_stats.py:138–184`):

- `cp_ref_area`: area-weighted mean of per-face `cp` over faces with positive area.
- `cp_ref_flux`: weighted by `Jw * area` over faces with `Jw > 0` only. Faces with `Jw <= 0` are counted (`n_faces_jw_nonpositive`, `area_jw_nonpositive`) and excluded from this weight. Raises if the positive-flux weight is not positive.

Ratios (`src/ro/cp_concentration_stats.py:191–234`), for a numerator that is either the area-mean `cm` or an area-weighted quantile of `cm`:

```
CP = (cm_num - cp_ref) / (c_b - cp_ref)
```

The function raises if `c_b - cp_ref` is not positive. It does not return an infinite per-face value.

Quantile: smallest `cm` whose cumulative area fraction is at least `p`, after sorting by `cm` (`src/ro/cp_concentration_stats.py:19–21`, `:100–113`). `p = 0.999` is `CP_q999`. `p = 0.99` is also computed and is not the paper metric (`docs/metrics_conventions.md:297–299`).

The window quantile is computed on the window face set. Per-cell quantiles are not averaged into it (`src/ro/cp_concentration_stats.py:248–249`).

Columns exist for both references: `cpc_window_avg_area`, `cpc_window_avg_flux`, `cp_q999_window_area`, `cp_q999_window_flux` (`src/ro/cp_concentration_stats.py:224–231`). The 2026-10-08 note says the meeting did not choose area versus flux (`docs/metrics_conventions.md:254–256`). That choice is **OPEN**. The MFBO extract profile’s required names are the flux columns; that is an extract-profile list, not a recorded paper decision.

`cp_canon_window_avg` is a different definition (scalar rescale against the mid-plane `c_b`). It remains in the extract. The paper metric is the concentration-statistics pair, not the canonical column (`docs/metrics_conventions.md:251–256`, superseded sentence).

### 6.6 Singularity of the per-face form

The UDM per-face value is `(cm - cp) / (c_0 - cp)` with `cp = B * cm / (Jw + B)` and `c_0 = C_INLET_REF` (`udfs/260822_RO_UDF.c:369–370`). That quotient is singular at

```
Jw = B * (cm / c_0 - 1)
```

(`docs/metrics_conventions.md:275–278`). Near contact lines `Jw` falls to about `2B` and the stored value diverges (`docs/metrics_conventions.md:264–268`). The paper averages and `CP_q999` do not use this per-face quotient. They use one reference `cp` in the denominator `c_b - cp_ref`, and they abort when that denominator is not positive (§6.5).

---

## 7. Convergence gate

`src/ro/convergence_quality.py`. A run can pass this gate with `stop_reason = max_iter_reached`, and a QoI stop can still fail the gate (`src/ro/convergence_quality.py:1–17`).

| Check | Threshold | Unit | On failure | Source |
| --- | --- | --- | --- | --- |
| `abs(lmh_mass_balance - lmh_udm_avg) / abs(lmh_udm_avg)` | 1e-3 | — | FAIL | `src/ro/convergence_quality.py:30` |
| Mass-balance relative error (boundary permeate vs `abs(volint total mass source)`) | 1e-3 | — | FAIL | `src/ro/convergence_quality.py:31`; definition `scripts/pyfluent_report_extract.py:2221–2227` |
| Final continuity residual | 1e-4 | — | FAIL | `src/ro/convergence_quality.py:32` |
| Pressure-drop relative spread over the evaluation window | none | — | WARNING only | `src/ro/convergence_quality.py:7–14` |

Water sink used in the balance is `volint(total) - volint(salt)` (`scripts/pyfluent_report_extract.py:2206–2213`). Continuity is read from the solve transcript, not from a Fluent report. The pressure-drop warning has no numeric limit (**OPEN** as a threshold).
