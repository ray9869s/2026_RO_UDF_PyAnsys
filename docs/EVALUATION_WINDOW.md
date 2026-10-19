# Evaluation window from development length

Generated 2026-10-11 by `scripts/mfbo/development_length.py`.
This file is output. Regenerate it with the script; do not hand-edit it.

Domain and CAD are unchanged. The per-cell CP excess is
`(c_m - c_p) / (c_b - c_p) - 1` from `summary_metrics_wide.csv`.
The mean is an unweighted mean of those cell values.

Downstream reference: mean of the cells in the last 6.930 mm,
and at least 2 cells.
Development distance `d` is the first active-zone cell boundary after
which every rolling mean stays within
3% of that reference.
The rolling window is the smallest whole number of spacer periods
that is at least 3 cells. It starts at the next
active cell after that boundary. `d` is the maximum over PASS
`u0p1_p6M`, `u0p2_p6M`, and `u0p3_p6M` leaves that exist.
A missing, FAIL, or UNKNOWN leaf is not an operating point.
An operating point whose rolling mean never stays in band has not
developed. That geometry uses `not_developed_fallback`: exclude to
the boundary at or just above `L_base`, and keep a whole number of
spacer periods. The window is never empty.

`L_base` = 10.395 mm (three 3.465 mm cells).
Cell boundaries are compared within 1e-9 m. A boundary on `L_base`
is `L_base` itself.
If that boundary is at or beyond `d`, exclude up to it.
Otherwise exclude up to the first cell boundary at or beyond
`max(L_base, d)`. The window is then a whole number of spacer periods.
Sinusoidal periods are the registry wavelength. Other families use
one mesh unit cell. The rolling window is the smallest multiple of
that period which is at least 3 cells.
The evaluation window is the remaining active cells.
A window of fewer than 3 spacer periods is flagged.
The window length in millimetres is still reported.

Production mesh id is `max085_min006_cpg5_bl4_peel2`.
`D0817_a60` uses `max060_min006_cpg5_bl4_peel2`.
The old bias excludes the first 3 active cells.
Bias is `(window mean excess - downstream reference) / reference`.
Negative bias means the window mean is below the reference.

## Flags

MFBO pillar ids use family default `n_lead_excluded=4`, `excluded_length_m=0.01386`: most conservative campaign pillar P_p60_h00 at u0p1_p6M. Campaign geo_ids keep their own rows. Diamond, ml, sin, and empty have no family default.

Window shorter than 3 spacer periods: `S_a072_l6930`, `S_a144_l6930`, `S_a193_l6930`.

Not developed at every PASS operating point: `S_a072_l6930`, `S_a193_l6930`.

## Exclusion

| geo_id | cell mm | period | n_active | d max mm | sets max | excluded cells | excluded mm | window cells | window mm | short | rule |
| --- | ---: | --- | ---: | ---: | --- | --- | ---: | --- | ---: | --- | --- |
| REF_empty | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p1_p6M, u0p2_p6M, u0p3_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| D2450_a30 | 2.829 | 1 cells, 2.829 mm, unit_cell | 9 | 14.146 | u0p2_p6M | 2-6 | 14.146 | 7-10 | 11.317 | no | at_least_max_l_base_d |
| D2450_a45 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p2_p6M, u0p3_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| D2450_a60 | 4.900 | 1 cells, 4.900 mm, unit_cell | 5 | 9.800 | u0p2_p6M, u0p3_p6M | 2-3 | 9.800 | 4-6 | 14.701 | no | boundary_below_l_base |
| D1225_a30 | 1.415 | 1 cells, 1.415 mm, unit_cell | 18 | 21.219 | u0p2_p6M | 2-16 | 21.219 | 17-19 | 4.244 | no | at_least_max_l_base_d |
| D1225_a45 | 1.732 | 1 cells, 1.732 mm, unit_cell | 14 | 15.592 | u0p2_p6M | 2-10 | 15.592 | 11-15 | 8.662 | no | at_least_max_l_base_d |
| D1225_a60 | 2.450 | 1 cells, 2.450 mm, unit_cell | 10 | 14.701 | u0p2_p6M, u0p3_p6M | 2-7 | 14.701 | 8-11 | 9.800 | no | at_least_max_l_base_d |
| D0817_a30 | 0.943 | 1 cells, 0.943 mm, unit_cell | 27 | 13.203 | u0p2_p6M | 2-15 | 13.203 | 16-28 | 12.260 | no | at_least_max_l_base_d |
| D0817_a45 | 1.155 | 1 cells, 1.155 mm, unit_cell | 21 | 10.395 | u0p1_p6M, u0p2_p6M, u0p3_p6M | 2-10 | 10.395 | 11-22 | 13.860 | no | boundary_below_l_base |
| D0817_a60 | 1.633 | 1 cells, 1.633 mm, unit_cell | 15 | 17.968 | u0p3_p6M | 2-12 | 17.968 | 13-16 | 6.534 | no | at_least_max_l_base_d |
| M_c160 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p3_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| M_c267 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 10.395 | u0p3_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| M_c400 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 6.930 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| P_p60_h00 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| P_p60_h15 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| P_p60_h30 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| P_p80_h00 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 10.395 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| P_p80_h15 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 10.395 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| P_p80_h30 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| P_p100_h00 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 3.465 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| P_p100_h15 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 3.465 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| P_p100_h30 | 3.465 | 1 cells, 3.465 mm, unit_cell | 7 | 6.930 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| S_a072_l1733 | 3.465 | 1 cells, 1.733 mm, wavelength | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| S_a072_l3465 | 3.465 | 1 cells, 3.465 mm, wavelength | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| S_a072_l6930 | 3.465 | 2 cells, 6.930 mm, wavelength | 7 | not developed | none | 2-4 | 10.395 | 5-8 | 13.860 | yes | not_developed_fallback |
| S_a144_l1733 | 3.465 | 1 cells, 1.733 mm, wavelength | 7 | 10.395 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| S_a144_l3465 | 3.465 | 1 cells, 3.465 mm, wavelength | 7 | 13.860 | u0p1_p6M, u0p2_p6M, u0p3_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| S_a144_l6930 | 3.465 | 2 cells, 6.930 mm, wavelength | 7 | 10.395 | u0p1_p6M, u0p2_p6M, u0p3_p6M | 2-4 | 10.395 | 5-8 | 13.860 | yes | boundary_below_l_base |
| S_a193_l1733 | 3.465 | 1 cells, 1.733 mm, wavelength | 7 | 10.395 | u0p1_p6M | 2-4 | 10.395 | 5-8 | 13.860 | no | boundary_below_l_base |
| S_a193_l3465 | 3.465 | 1 cells, 3.465 mm, wavelength | 7 | 13.860 | u0p1_p6M | 2-5 | 13.860 | 6-8 | 10.395 | no | at_least_max_l_base_d |
| S_a193_l6930 | 3.465 | 2 cells, 6.930 mm, wavelength | 7 | not developed | none | 2-4 | 10.395 | 5-8 | 13.860 | yes | not_developed_fallback |

## Operating points

| geo_id | run_id | d mm | sets max | reference cells | max rel after d | breach before d | bias old 3-cell | bias new window |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| REF_empty | u0p1_p6M | 13.860 | yes | 2 | +2.68% | +35.15% | -5.71% | -2.68% |
| REF_empty | u0p2_p6M | 13.860 | yes | 2 | +2.76% | +35.80% | -5.88% | -2.76% |
| REF_empty | u0p3_p6M | 13.860 | yes | 2 | +2.78% | +36.02% | -5.93% | -2.78% |
| D2450_a30 | u0p1_p6M | 5.658 | no | 2 | +1.68% | +7.11% | -0.16% | -0.14% |
| D2450_a30 | u0p2_p6M | 14.146 | yes | 2 | +2.20% | +7.32% | +4.85% | +1.26% |
| D2450_a30 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| D2450_a45 | u0p1_p6M | 0.000 | no | 2 | +1.60% | +0.00% | -0.51% | -0.36% |
| D2450_a45 | u0p2_p6M | 13.860 | yes | 2 | +1.24% | +2.51% | +2.66% | +1.24% |
| D2450_a45 | u0p3_p6M | 13.860 | yes | 2 | +2.24% | +15.33% | +7.45% | +2.24% |
| D2450_a60 | u0p1_p6M | 4.900 | no | 2 | +1.59% | +6.36% | +0.00% | -0.35% |
| D2450_a60 | u0p2_p6M | 9.800 | yes | 2 | +1.76% | +13.85% | +0.00% | -1.76% |
| D2450_a60 | u0p3_p6M | 9.800 | yes | 2 | +2.98% | +18.36% | +0.00% | -2.98% |
| D1225_a30 | u0p1_p6M | 8.487 | no | 4 | +1.92% | +16.23% | +1.29% | -0.73% |
| D1225_a30 | u0p2_p6M | 21.219 | yes | 4 | +0.78% | +6.23% | -2.78% | +0.78% |
| D1225_a30 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| D1225_a45 | u0p1_p6M | 8.662 | no | 4 | +1.63% | +22.29% | -2.10% | -0.24% |
| D1225_a45 | u0p2_p6M | 15.592 | yes | 4 | +1.49% | +41.12% | -6.59% | -0.62% |
| D1225_a45 | u0p3_p6M | 8.662 | no | 4 | +2.23% | +46.62% | -3.53% | +0.45% |
| D1225_a60 | u0p1_p6M | 7.350 | no | 2 | +2.94% | +18.27% | -1.18% | +0.13% |
| D1225_a60 | u0p2_p6M | 14.701 | yes | 2 | +1.57% | +23.41% | -3.43% | -1.13% |
| D1225_a60 | u0p3_p6M | 14.701 | yes | 2 | +2.01% | +26.48% | -4.57% | -1.42% |
| D0817_a30 | u0p1_p6M | 11.317 | no | 7 | +2.71% | +40.33% | -4.74% | -0.20% |
| D0817_a30 | u0p2_p6M | 13.203 | yes | 7 | +2.61% | +50.20% | -7.20% | -0.77% |
| D0817_a30 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| D0817_a45 | u0p1_p6M | 10.395 | yes | 6 | +1.43% | +33.47% | -3.74% | -0.43% |
| D0817_a45 | u0p2_p6M | 10.395 | yes | 6 | +2.58% | +41.19% | -4.76% | -0.96% |
| D0817_a45 | u0p3_p6M | 10.395 | yes | 6 | +2.40% | +40.99% | -4.84% | -1.04% |
| D0817_a60 | u0p1_p6M | 13.067 | no | 4 | +1.10% | +36.46% | -5.22% | +0.00% |
| D0817_a60 | u0p2_p6M | 13.067 | no | 4 | +1.33% | +38.54% | -5.63% | +0.00% |
| D0817_a60 | u0p3_p6M | 17.968 | yes | 4 | +0.23% | +39.64% | -5.00% | +0.00% |
| M_c160 | u0p1_p6M | 3.465 | no | 2 | +2.75% | +6.82% | -0.27% | -0.12% |
| M_c160 | u0p2_p6M | 3.465 | no | 2 | +0.56% | +2.83% | +0.21% | +0.17% |
| M_c160 | u0p3_p6M | 13.860 | yes | 2 | +1.62% | +10.17% | +3.77% | +1.62% |
| M_c267 | u0p1_p6M | 3.465 | no | 2 | +1.62% | +4.55% | -0.14% | -0.14% |
| M_c267 | u0p2_p6M | 3.465 | no | 2 | +0.82% | +3.64% | +0.07% | +0.07% |
| M_c267 | u0p3_p6M | 10.395 | yes | 2 | +1.03% | +3.83% | +0.81% | +0.81% |
| M_c400 | u0p1_p6M | 6.930 | yes | 2 | +1.27% | +7.36% | -0.37% | -0.37% |
| M_c400 | u0p2_p6M | 3.465 | no | 2 | +1.04% | +5.21% | +0.10% | +0.10% |
| M_c400 | u0p3_p6M | 3.465 | no | 2 | +0.99% | +2.57% | +0.32% | +0.32% |
| P_p60_h00 | u0p1_p6M | 13.860 | yes | 2 | +1.20% | +22.12% | -2.64% | -1.20% |
| P_p60_h00 | u0p2_p6M | 3.465 | no | 2 | +0.75% | +0.87% | +0.30% | +0.19% |
| P_p60_h00 | u0p3_p6M | 3.465 | no | 2 | +0.82% | +0.05% | +0.01% | +0.09% |
| P_p60_h15 | u0p1_p6M | 13.860 | yes | 2 | +1.17% | +22.51% | -2.69% | -1.17% |
| P_p60_h15 | u0p2_p6M | 3.465 | no | 2 | +1.29% | +4.11% | +0.21% | +0.06% |
| P_p60_h15 | u0p3_p6M | 0.000 | no | 2 | +1.96% | +0.00% | +0.08% | +0.13% |
| P_p60_h30 | u0p1_p6M | 13.860 | yes | 2 | +1.36% | +24.41% | -3.06% | -1.36% |
| P_p60_h30 | u0p2_p6M | 3.465 | no | 2 | +0.84% | +3.78% | +0.10% | +0.10% |
| P_p60_h30 | u0p3_p6M | 0.000 | no | 2 | +1.24% | +0.00% | -0.00% | -0.05% |
| P_p80_h00 | u0p1_p6M | 10.395 | yes | 2 | +2.41% | +16.51% | -1.69% | -1.69% |
| P_p80_h00 | u0p2_p6M | 0.000 | no | 2 | +2.07% | +0.00% | -0.05% | -0.05% |
| P_p80_h00 | u0p3_p6M | 6.930 | no | 2 | +2.47% | +0.79% | -0.95% | -0.95% |
| P_p80_h15 | u0p1_p6M | 10.395 | yes | 2 | +2.41% | +17.26% | -1.72% | -1.72% |
| P_p80_h15 | u0p2_p6M | 3.465 | no | 2 | +1.42% | +0.20% | -0.18% | -0.18% |
| P_p80_h15 | u0p3_p6M | 3.465 | no | 2 | +1.47% | +0.32% | -0.12% | -0.12% |
| P_p80_h30 | u0p1_p6M | 13.860 | yes | 2 | +1.06% | +20.09% | -2.38% | -1.06% |
| P_p80_h30 | u0p2_p6M | 0.000 | no | 2 | +1.75% | +0.00% | +0.02% | +0.02% |
| P_p80_h30 | u0p3_p6M | 0.000 | no | 2 | +1.25% | +0.00% | +0.04% | +0.08% |
| P_p100_h00 | u0p1_p6M | 3.465 | yes | 2 | +0.92% | +2.57% | -0.17% | -0.17% |
| P_p100_h00 | u0p2_p6M |  |  |  |  |  |  | not used (FAIL) |
| P_p100_h00 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| P_p100_h15 | u0p1_p6M | 3.465 | yes | 2 | +0.73% | +3.27% | +0.23% | +0.23% |
| P_p100_h15 | u0p2_p6M | 0.000 | no | 2 | +1.84% | +0.00% | -0.25% | -0.25% |
| P_p100_h15 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| P_p100_h30 | u0p1_p6M | 6.930 | yes | 2 | +1.16% | +8.00% | -0.28% | -0.28% |
| P_p100_h30 | u0p2_p6M | 0.000 | no | 2 | +0.39% | +0.00% | +0.03% | +0.03% |
| P_p100_h30 | u0p3_p6M | 0.000 | no | 2 | +1.62% | +0.00% | +0.65% | +0.65% |
| S_a072_l1733 | u0p1_p6M | 13.860 | yes | 2 | +1.78% | +24.55% | -3.44% | -1.78% |
| S_a072_l1733 | u0p2_p6M | 6.930 | no | 2 | +2.34% | +13.09% | -0.97% | -0.41% |
| S_a072_l1733 | u0p3_p6M | 6.930 | no | 2 | +1.49% | +7.76% | -0.78% | -0.30% |
| S_a072_l3465 | u0p1_p6M | 13.860 | yes | 2 | +1.06% | +27.22% | -2.84% | -1.06% |
| S_a072_l3465 | u0p2_p6M | 6.930 | no | 2 | +2.16% | +17.71% | +0.30% | +0.64% |
| S_a072_l3465 | u0p3_p6M | 6.930 | no | 2 | +1.32% | +12.21% | +0.45% | +0.32% |
| S_a072_l6930 | u0p1_p6M | not developed | no | 2 | +28.09% | +25.09% | -3.74% | -3.74% |
| S_a072_l6930 | u0p2_p6M | 10.395 | no | 2 | +1.83% | +21.16% | -1.83% | -1.83% |
| S_a072_l6930 | u0p3_p6M | 3.465 | no | 2 | +2.77% | +11.30% | +1.72% | +1.72% |
| S_a144_l1733 | u0p1_p6M | 10.395 | yes | 2 | +1.52% | +18.51% | -1.03% | -1.03% |
| S_a144_l1733 | u0p2_p6M | 3.465 | no | 2 | +2.26% | +7.37% | +0.26% | +0.26% |
| S_a144_l1733 | u0p3_p6M |  |  |  |  |  |  | not used (FAIL) |
| S_a144_l3465 | u0p1_p6M | 13.860 | yes | 2 | +2.08% | +25.44% | -4.04% | -2.08% |
| S_a144_l3465 | u0p2_p6M | 13.860 | yes | 2 | +2.11% | +16.37% | -3.21% | -2.11% |
| S_a144_l3465 | u0p3_p6M | 13.860 | yes | 2 | +1.40% | +9.31% | -2.17% | -1.40% |
| S_a144_l6930 | u0p1_p6M | 10.395 | yes | 2 | +2.93% | +23.46% | -2.93% | -2.93% |
| S_a144_l6930 | u0p2_p6M | 10.395 | yes | 2 | +1.22% | +19.88% | -1.22% | -1.22% |
| S_a144_l6930 | u0p3_p6M | 10.395 | yes | 2 | +2.80% | +9.55% | +2.80% | +2.80% |
| S_a193_l1733 | u0p1_p6M | 10.395 | yes | 2 | +2.04% | +16.17% | -1.35% | -1.35% |
| S_a193_l1733 | u0p2_p6M | 3.465 | no | 2 | +1.88% | +4.45% | -0.29% | -0.29% |
| S_a193_l1733 | u0p3_p6M | 3.465 | no | 2 | +2.66% | +2.25% | -0.77% | -0.77% |
| S_a193_l3465 | u0p1_p6M | 13.860 | yes | 2 | +0.81% | +24.94% | -2.32% | -0.81% |
| S_a193_l3465 | u0p2_p6M | 10.395 | no | 2 | +2.40% | +16.56% | -1.50% | -0.56% |
| S_a193_l3465 | u0p3_p6M | 6.930 | no | 2 | +1.19% | +10.10% | +0.94% | +0.70% |
| S_a193_l6930 | u0p1_p6M | 10.395 | no | 2 | +2.61% | +22.88% | -2.61% | -2.61% |
| S_a193_l6930 | u0p2_p6M | 10.395 | no | 2 | +0.79% | +19.53% | -0.79% | -0.79% |
| S_a193_l6930 | u0p3_p6M | not developed | no | 2 | +9.27% | +6.27% | +5.18% | +5.18% |

## Sources

| geo_id | mesh_id | data root |
| --- | --- | --- |
| REF_empty | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D2450_a30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D2450_a45 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D2450_a60 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D1225_a30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D1225_a45 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D1225_a60 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D0817_a30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D0817_a45 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| D0817_a60 | max060_min006_cpg5_bl4_peel2 | C:/ro_data |
| M_c160 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| M_c267 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| M_c400 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p60_h00 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p60_h15 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p60_h30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p80_h00 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p80_h15 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p80_h30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p100_h00 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p100_h15 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| P_p100_h30 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a072_l1733 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a072_l3465 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a072_l6930 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a144_l1733 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a144_l3465 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a144_l6930 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a193_l1733 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a193_l3465 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
| S_a193_l6930 | max085_min006_cpg5_bl4_peel2 | C:/ro_data |
