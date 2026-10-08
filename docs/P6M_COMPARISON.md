# p6M spacer comparison, read 2026-10-03

Measured comparison of the production p6M column. This file records that
read. It does not choose a next operating point.

Source, on the Windows data root, without launching Fluent: each production
leaf's `post/reports/summary_metrics_wide.csv`, accepted only when the run
`manifest.json` has `convergence_quality = PASS`. Production mesh is
`max085_min006_cpg5_bl4_peel2`, except `D0817_a60`, which is
`max060_min006_cpg5_bl4_peel2`. Run ids are exactly `u0p1_p6M`, `u0p2_p6M`,
and `u0p3_p6M`. Grid-study meshes and restart leaves are not in the tables.
`S_a144_l1733` has a usable `u0p3` restart outside this run id
(`docs/STATUS_2026-09-22.md`); that leaf is not included, so the 0.3 m/s
table has 86 PASS rows rather than the 87 usable solves in the status note.
The six rejected original leaves match that note.

Ranking metric is `cp_canon_window_avg - 1` (lower excess is less
polarization). Pressure is `pressure_drop_spacer_per_m` divided by
`REF_empty` at the same velocity. Absolute pascal drop is not used: active
length differs by pitch. LMH is `lmh_mass_balance` over the empty channel
at the same velocity. That LMH is per exposed membrane area (`area_mem`).
A 2026-10 read of the same column finds that, inside the pillar family,
per-module-area ranking reverses the exposed-area ranking. The numbers
are in `docs/metrics_conventions.md`. The CP rank tables below are
unchanged. `cp_canon_window_max`, inlet CP, L1, and L2 are not
ranks.

**2026-10-08 meeting.** Later comparison uses the concentration-statistics
average and `CP_q999` (`docs/metrics_conventions.md`). These tables stay
on `cp_canon_window_avg - 1`. The same meeting says to report both
exposed-area and module-area LMH. These LMH columns stay on exposed area.

`REF_empty` dP/L is 3707.5 Pa/m at 0.2 m/s and 5634.2 Pa/m at 0.3 m/s, the
same extracts as `docs/metrics_conventions.md`.

## What is better

Converged pillars have the lowest CP excess at every speed. The reported
leader changes because the previous leader has no PASS row at the next
speed, not because a worse pillar overtakes it.

| velocity | PASS spacers | lowest CP−1 | CP−1 | × empty CP excess | × empty dP/L | × empty LMH |
|---|---:|---|---:|---:|---:|---:|
| 0.1 m/s | 30 | `P_p100_h00` | 0.0581 | 0.310 | 17.4 | 1.116 |
| 0.2 m/s | 29 | `P_p100_h15` | 0.0310 | 0.207 | 25.0 | 1.105 |
| 0.3 m/s | 24 | `P_p100_h30` | 0.0273 | 0.209 | 26.3 | 1.090 |

`P_p100_h00` is PASS only at 0.1 m/s. `P_p100_h15` is PASS at 0.1 and 0.2 m/s.
Every pillar that does converge stays in the top of that speed's list: all
nine at 0.1 m/s are inside the top 15 of 30; the eight at 0.2 m/s are inside
the top 9 of 29; the seven at 0.3 m/s are the top 7 of 24.

Inside the pillar family the two axes separate. A larger pillar diameter
lowers CP excess. Opening the bore from 0 to 0.30 mm raises CP excess a
little and lowers the pressure multiplier. At 0.1 m/s, `P_p100` goes
0.0581 / 17.4× (`h00`) to 0.0694 / 14.9× (`h30`). The same direction holds
at `P_p80`, `P_p60`, and at the other speeds where all three bores converge.
`P_p60` sits at the worse end of the pillar CP list. At that diameter the
bore is not a separate jet orifice (`docs/GEOMETRY_DESIGN.md`).

`D2450_a45`, the historical reference, is rank 8, 13, and 11 in the three
PASS lists. It is not the campaign minimum.

The long-wave sinusoids are the high-CP end at every speed. At 0.1 m/s,
`S_*_l6930` are the bottom three, with CP excess 1.06–1.08 times the empty
channel and LMH 0.988–0.989 times the empty channel. They are the only
spacers below the empty-channel flux. At 0.2 and 0.3 m/s they remain the
bottom three of those PASS lists, still near the empty-channel CP excess
(0.96–0.98 and 0.75–0.80) and only 0.3–2.5% above empty LMH.

Wavelength, not amplitude, orders the sinusoidal family. At each speed,
`l1733` has lower CP excess than `l3465`, which has lower CP excess than
`l6930`. Amplitude splits the short wave (`a193` below `a144` below `a072`)
and barely splits `l6930` (0.1995, 0.2004, 0.2015 at 0.1 m/s).

Within multi-layer, the design expectation holds at all three speeds:
`M_c400` has the lowest CP excess and the highest pressure multiplier, then
`M_c267`, then `M_c160`. At 0.1 m/s the three CP excesses are 0.0794,
0.0862, and 0.0965, with pressure multipliers 17.1, 10.3, and 8.2. Against
the other families, `M_c400` does not stay at rank 4: shared-set place 3 at
0.1 m/s, place 10 at 0.2 m/s, place 8 at 0.3 m/s.

`D2450_a60` has the lowest pressure multiplier in the whole PASS set at
every speed (3.66, 4.45, 5.10) and a poor CP rank (20, 26, 19 in the
printed lists). `D0817_a30` has the highest (62.3 at 0.1 m/s, 73.7 at
0.2 m/s) and only mid-list CP (printed ranks 16 and 17). It has no PASS
row at 0.3 m/s. `D0817_a60` uses the finer production mesh; its CP sits
with the other 60° diamonds, so the mesh exception does not put it in a
different part of the list.

## Does rank change with velocity

Printed ranks use that speed's own PASS count (30, 29, 24), so a printed
move mixes reordering with geometries that drop out. Re-ranking only the
geometries present at both speeds:

| pair | shared spacers | Spearman of CP−1 order | largest shared-set move |
|---|---:|---:|---|
| 0.1 vs 0.2 m/s | 29 | 0.908 | `M_c400` 3 → 10; `D2450_a60` 19 → 26; `D2450_a45` 7 → 13; `P_p60_*` climb about 5 |
| 0.2 vs 0.3 m/s | 24 | 0.988 | 2 places (`S_a144_l3465`, `S_a072_l3465`, and four others) |
| 0.1 vs 0.3 m/s | 24 | 0.876 | `M_c400` 2 → 8; `S_a072_l1733` 18 → 13 |

Family position is stable: converged pillars stay at the low-CP end,
`l6930` stays at the high-CP end. The individual leader changes with speed
because `P_p100_h00` and then `P_p100_h15` leave the PASS set. Among
geometries that still converge, 0.2 m/s and 0.3 m/s are almost the same
order. The real reshuffle is from 0.1 to 0.2 m/s: `M_c400` and the
`D2450` 45°/60° cases lose places, and the smaller pillars gain places.

Outlet pressure was not varied. The earlier argument that CP ranking should
be invariant in gauge pressure (4 / 6 / 8 MPa) is still untested. Inlet
velocity is a different axis, and on that axis the leader and some
mid-list places do change.

## CP excess and pressure loss

Pearson of (CP−1, dP / empty) on the spacer PASS rows is −0.326 at
0.1 m/s, −0.379 at 0.2 m/s, and −0.661 at 0.3 m/s. Higher pressure loss
associates with lower polarization, and the association is tighter at
higher speed.

It is not a proportional trade. `D0817_a30` spends 62–74 times the empty
pressure for a mid-list CP. `D1225_a30` spends 35–49 times and is better
(printed ranks 9 and 6) but still far from the pillar CP, and it does not
converge at 0.3 m/s. `P_p100_h30` reaches the lowest PASS CP at 0.3 m/s at
26 times empty pressure. `D2450_a60` and `l6930` show the other corner:
pressure multipliers of about 4–7 with CP near or above the empty channel.

Sorted by rising CP−1, LMH / empty falls from 1.116 to 0.989 at 0.1 m/s,
from 1.105 to 1.003 at 0.2 m/s, and from 1.090 to 1.019 at 0.3 m/s, with
only small local swaps. Flux separates the geometries by a few percent.
Pressure separates them by an order of magnitude. Under this shared
channel, mesh recipe, UDF, window, and CP definition, geometry shows up
first in polarization and pressure, not in a large flux gain.

Empty-channel CP itself falls with speed: 1.1875, 1.1501, 1.1309.

## PASS tables

Rank 1 is the lowest CP−1. `CP/empty` is CP excess over the empty-channel
CP excess. `dP/empty` and `LMH/empty` are ratios to `REF_empty` at the
same velocity.

### 0.1 m/s

Empty: CP 1.1875, dP/L 1822.2 Pa/m, LMH 23.2033. Thirty spacers.

| rank | geo_id | CP−1 | CP/empty | dP/empty | LMH/empty |
|---:|---|---:|---:|---:|---:|
| 1 | P_p100_h00 | 0.0581 | 0.310 | 17.355 | 1.116 |
| 2 | P_p100_h15 | 0.0605 | 0.323 | 17.124 | 1.114 |
| 3 | P_p100_h30 | 0.0694 | 0.370 | 14.857 | 1.106 |
| 4 | M_c400 | 0.0794 | 0.423 | 17.125 | 1.094 |
| 5 | P_p80_h00 | 0.0820 | 0.437 | 14.409 | 1.095 |
| 6 | P_p80_h15 | 0.0839 | 0.448 | 14.233 | 1.093 |
| 7 | M_c267 | 0.0862 | 0.460 | 10.313 | 1.085 |
| 8 | D2450_a45 | 0.0868 | 0.463 | 6.594 | 1.084 |
| 9 | D1225_a30 | 0.0898 | 0.479 | 34.888 | 1.081 |
| 10 | P_p80_h30 | 0.0917 | 0.489 | 12.478 | 1.086 |
| 11 | D2450_a30 | 0.0939 | 0.501 | 11.912 | 1.076 |
| 12 | M_c160 | 0.0965 | 0.515 | 8.189 | 1.075 |
| 13 | P_p60_h00 | 0.0978 | 0.522 | 12.395 | 1.081 |
| 14 | P_p60_h15 | 0.0989 | 0.528 | 12.252 | 1.080 |
| 15 | P_p60_h30 | 0.1060 | 0.566 | 10.852 | 1.074 |
| 16 | D0817_a30 | 0.1084 | 0.578 | 62.336 | 1.064 |
| 17 | D0817_a45 | 0.1126 | 0.601 | 28.264 | 1.060 |
| 18 | D1225_a45 | 0.1185 | 0.632 | 13.792 | 1.054 |
| 19 | S_a193_l1733 | 0.1211 | 0.646 | 15.506 | 1.054 |
| 20 | D2450_a60 | 0.1267 | 0.676 | 3.655 | 1.045 |
| 21 | S_a144_l1733 | 0.1299 | 0.693 | 14.960 | 1.047 |
| 22 | D0817_a60 | 0.1326 | 0.707 | 16.648 | 1.041 |
| 23 | D1225_a60 | 0.1365 | 0.728 | 7.795 | 1.037 |
| 24 | S_a072_l1733 | 0.1404 | 0.749 | 14.522 | 1.039 |
| 25 | S_a144_l3465 | 0.1605 | 0.856 | 7.881 | 1.020 |
| 26 | S_a193_l3465 | 0.1623 | 0.866 | 7.937 | 1.020 |
| 27 | S_a072_l3465 | 0.1638 | 0.874 | 7.897 | 1.019 |
| 28 | S_a193_l6930 | 0.1995 | 1.064 | 4.835 | 0.989 |
| 29 | S_a144_l6930 | 0.2004 | 1.069 | 4.835 | 0.988 |
| 30 | S_a072_l6930 | 0.2015 | 1.075 | 4.863 | 0.989 |

### 0.2 m/s

Empty: CP 1.1501, dP/L 3707.5 Pa/m, LMH 24.0506. Twenty-nine spacers.
`P_p100_h00` is not PASS.

| rank | geo_id | CP−1 | CP/empty | dP/empty | LMH/empty |
|---:|---|---:|---:|---:|---:|
| 1 | P_p100_h15 | 0.0310 | 0.207 | 25.022 | 1.105 |
| 2 | P_p100_h30 | 0.0327 | 0.218 | 20.788 | 1.104 |
| 3 | P_p80_h00 | 0.0342 | 0.228 | 20.275 | 1.102 |
| 4 | P_p80_h15 | 0.0351 | 0.234 | 19.951 | 1.101 |
| 5 | P_p80_h30 | 0.0374 | 0.249 | 16.777 | 1.099 |
| 6 | D1225_a30 | 0.0408 | 0.272 | 48.685 | 1.095 |
| 7 | P_p60_h00 | 0.0416 | 0.277 | 16.859 | 1.095 |
| 8 | P_p60_h15 | 0.0450 | 0.300 | 16.488 | 1.092 |
| 9 | P_p60_h30 | 0.0487 | 0.324 | 14.068 | 1.088 |
| 10 | M_c400 | 0.0516 | 0.344 | 25.034 | 1.085 |
| 11 | D2450_a30 | 0.0535 | 0.357 | 19.712 | 1.080 |
| 12 | M_c267 | 0.0583 | 0.389 | 15.049 | 1.077 |
| 13 | D2450_a45 | 0.0592 | 0.394 | 9.411 | 1.076 |
| 14 | M_c160 | 0.0606 | 0.404 | 11.705 | 1.075 |
| 15 | S_a193_l1733 | 0.0609 | 0.406 | 21.322 | 1.075 |
| 16 | S_a144_l1733 | 0.0691 | 0.460 | 20.319 | 1.068 |
| 17 | D0817_a30 | 0.0745 | 0.496 | 73.670 | 1.063 |
| 18 | D0817_a45 | 0.0765 | 0.509 | 32.808 | 1.061 |
| 19 | S_a072_l1733 | 0.0789 | 0.525 | 19.359 | 1.060 |
| 20 | D1225_a45 | 0.0847 | 0.564 | 16.705 | 1.055 |
| 21 | S_a193_l3465 | 0.0913 | 0.608 | 10.328 | 1.048 |
| 22 | S_a144_l3465 | 0.0964 | 0.642 | 10.214 | 1.042 |
| 23 | D0817_a60 | 0.0968 | 0.645 | 18.168 | 1.042 |
| 24 | D1225_a60 | 0.0995 | 0.663 | 8.762 | 1.039 |
| 25 | S_a072_l3465 | 0.1013 | 0.674 | 10.225 | 1.039 |
| 26 | D2450_a60 | 0.1034 | 0.689 | 4.453 | 1.037 |
| 27 | S_a072_l6930 | 0.1440 | 0.959 | 5.894 | 1.006 |
| 28 | S_a144_l6930 | 0.1456 | 0.970 | 5.842 | 1.003 |
| 29 | S_a193_l6930 | 0.1467 | 0.977 | 5.835 | 1.003 |

### 0.3 m/s

Empty: CP 1.1309, dP/L 5634.2 Pa/m, LMH 24.4847. Twenty-four spacers.
Not PASS at this run id: `D0817_a30`, `D1225_a30`, `D2450_a30`,
`P_p100_h00`, `P_p100_h15`, `S_a144_l1733`.

| rank | geo_id | CP−1 | CP/empty | dP/empty | LMH/empty |
|---:|---|---:|---:|---:|---:|
| 1 | P_p100_h30 | 0.0273 | 0.209 | 26.310 | 1.090 |
| 2 | P_p80_h00 | 0.0281 | 0.214 | 25.985 | 1.090 |
| 3 | P_p80_h15 | 0.0290 | 0.221 | 25.210 | 1.089 |
| 4 | P_p80_h30 | 0.0295 | 0.226 | 20.918 | 1.088 |
| 5 | P_p60_h00 | 0.0306 | 0.234 | 20.935 | 1.087 |
| 6 | P_p60_h15 | 0.0312 | 0.239 | 20.369 | 1.086 |
| 7 | P_p60_h30 | 0.0321 | 0.245 | 17.083 | 1.085 |
| 8 | M_c400 | 0.0376 | 0.287 | 32.305 | 1.080 |
| 9 | M_c267 | 0.0417 | 0.319 | 19.645 | 1.074 |
| 10 | M_c160 | 0.0419 | 0.320 | 15.511 | 1.073 |
| 11 | D2450_a45 | 0.0423 | 0.323 | 12.344 | 1.073 |
| 12 | S_a193_l1733 | 0.0440 | 0.336 | 26.999 | 1.074 |
| 13 | S_a072_l1733 | 0.0542 | 0.414 | 23.895 | 1.064 |
| 14 | D0817_a45 | 0.0580 | 0.443 | 37.775 | 1.061 |
| 15 | S_a144_l3465 | 0.0641 | 0.490 | 12.383 | 1.054 |
| 16 | S_a193_l3465 | 0.0642 | 0.490 | 12.568 | 1.057 |
| 17 | D1225_a45 | 0.0644 | 0.492 | 20.178 | 1.057 |
| 18 | S_a072_l3465 | 0.0680 | 0.520 | 12.365 | 1.052 |
| 19 | D2450_a60 | 0.0770 | 0.589 | 5.102 | 1.047 |
| 20 | D0817_a60 | 0.0784 | 0.599 | 19.744 | 1.042 |
| 21 | D1225_a60 | 0.0815 | 0.623 | 9.608 | 1.039 |
| 22 | S_a072_l6930 | 0.0986 | 0.754 | 6.887 | 1.025 |
| 23 | S_a144_l6930 | 0.1038 | 0.793 | 6.813 | 1.020 |
| 24 | S_a193_l6930 | 0.1051 | 0.803 | 6.803 | 1.019 |
