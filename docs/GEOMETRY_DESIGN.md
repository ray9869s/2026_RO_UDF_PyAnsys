# Geometry design notes

Design decisions for spacer CAD and mesh aids. Code registries:
`src/ro/campaign_geometry.py`, `src/ro/campaign_geo_ids.py`,
`configs/batch_config.py`. Channel height \(h = 0.770\,\mathrm{mm}\) for all
campaign families.

---

## Joint sphere (filament–filament contact)

The sphere fills the cusp where two cylinders cross. Its size should be set by
**how far the cusp extends**, not by a ratio to any single filament radius.

Near tangency the gap is a paraboloid. Shallow principal radius \(R_s\):

\[
\frac{1}{R_s}
=
\frac{
  (1/r_a + 1/r_b)
  - \sqrt{1/r_a^2 + 1/r_b^2 + 2\cos(2\gamma)/(r_a r_b)}
}{2}
\]

with \(\gamma\) the crossing angle. Covering the region where the gap is below
threshold \(g\) needs

\[
R = \sqrt{2 R_s g}.
\]

**Upper bound (45° intersection):** the sphere centre sits on the cylinder
surface, so at the extreme of the sphere–cylinder intersection the normals
meet at \(\cos\theta = R/(2r)\). \(\theta\) below 45° makes a sliver, capping

\[
R \le 1.414\, r_{\min}.
\]

| family | crossing | \(R_s\) (mm) | \(R\) (mm) | \(g\) (µm) |
|--------|----------|-------------:|-----------:|-----------:|
| Diamond | 90° equal 0.200 | 0.200 | 0.110 | 30.3 |
| M_c160 | 45° | 0.4189 | 0.100 | 11.9 |
| M_c267 | 45° | 0.4552 | 0.105 | 12.1 |
| M_c400 | 45° | 0.5236 | 0.110 | 11.6 |

### ML

Uses \(g = 12\,\mu\mathrm{m}\) (\(2 \times m_{\min}\)), the largest value both
bounds allow on all three: c160's 45° cap is 0.1131 mm and \(g = 18\,\mu\mathrm{m}\)
would need \(R = 0.1228\). \(R > r_{\min}\) on c160 and c400 is intentional —
spheres and filaments are boolean-subtracted from a solid box, so a sphere
reaching past the middle-layer surface only changes the outer fluid boundary.
Do not reinstate \(R < r_{\min}\) or sphere–sphere non-overlap guards (rule 3-6
keeps only the \(R = R_{\mathrm{ratio}} \times r_{\min}\) identity).

**M_c400 second upper bound (empirical, two points, no derivation):**
\(R = 0.112\) broke Set Up Periodic Boundaries after surface meshing
(skew 0.60108, sff 0, 485,834 faces) — unpaired
`curve-network-5454:5455` after four shadow-zone copies; two runs identical.
\(R = 0.110\) builds. c400 is the only ML case with \(R > r_{\mathrm{outer}}\)
(\(R/r_{\mathrm{outer}} = 0.625 / 0.787 / 1.10\) for c160 / c267 / c400).
Revisit if mesh settings change. \(R = 0.110\) back-solves to
\(g = 0.110^2 / (2 \times 0.5236) = 11.55\,\mu\mathrm{m}\) (1 µm short of
the 12 µm target).

**Evidence the rule works:** M_c267 went from ortho 0.00940 (failing the 0.05
gate) at \(R = 0.110\) to ortho 0.07496 at \(R = 0.105\).

### Diamond

\(g = 30\,\mu\mathrm{m}\) is **not derived** from the wedge rule; it predates
it. Leave it: all nine Diamond meshes pass (ortho 0.068–0.115); the brg156
trial (\(g = 61\,\mu\mathrm{m}\)) made things worse (D1225_a60 ortho
0.0762 → 0.0734, AR 79.4 → 120.9; D2450_a60 ortho 0.0849 → 0.0804), so
\(g = 30\,\mu\mathrm{m}\) is already in a saturated regime; remeshing would
invalidate D2450_a45 verified references.

The per-family difference in \(g\) is defensible: the sphere is a meshing aid,
and each family uses the smallest size that clears the wedge subject to its
constraints. ML is limited by c160's thin middle layer (and c400's periodic
pairing ceiling); Diamond has no such constraint.

Manifest fields: `joint_sphere_R_m`, `joint_sphere_r_min_m`,
`joint_sphere_R_ratio` (= \(R / r_{\min}\) exactly), `joint_sphere_z_m`,
`joint_sphere_count`. Mesh cases also carry `bridge_radius_m` (= \(R\) for ML).

---

## Pillar bore axis

Bore diameters \(\{0,\,0.15,\,0.30\}\,\mathrm{mm}\) (geo tokens `h00` / `h15` /
`h30`), absolute — not a ratio of \(D_p\) — so the same jet orifice is tested
at every pillar size. Changed from \(\{0,\,0.20,\,0.30\}\) because **P_p80_h20
would not mesh**.

### Arc gap on the pillar surface

At \(z = 0\) each opening has half-angle \(\arcsin(r/R_p)\). Filaments at
\(\pm 45^\circ\), bore at \(0\):

\[
\text{gap} = R_p \bigl(
  45^\circ - \arcsin(r_f/R_p) - \arcsin(r_h/R_p)
\bigr)
\]

(µm; positive = separate openings, negative = merged):

| | h15 | h20 | h30 |
|--|----:|----:|----:|
| p60 \(R=0.300\) | −59.1 | −85.3 | −140.4 |
| p80 \(R=0.400\) | +29.3 | **+3.65** | −49.0 |
| p100 \(R=0.500\) | +111.7 | +86.3 | +34.6 |

p80_h20's 3.65 µm is below \(m_{\min} = 6\,\mu\mathrm{m}\). No mesh setting
resolves that sliver. Four attempts vs skew gate 0.85:

| attempt | skew |
|---------|-----:|
| old CAD, cpg5 | 0.893 |
| rebuilt CAD, cpg5 | 0.901 |
| rebuilt CAD, `m_min` 0.003 | 0.897 |
| rebuilt CAD, `m_cpg` 7 | 0.890 |

Every case that meshed has either merged openings or a gap ≥ 34.6 µm.
Forcing a merge needs \(d_h \ge 0.207\); a barely-merged pair meets at a cusp.
Safe merge is ~0.28–0.30 (h30). Middle level therefore moved to 0.15 mm
(smallest remaining gap 29 µm on p80_h15).

### \(D_p = 0.60\) interpretation

The two filament openings are only 33 µm apart on the pillar surface
(\(90^\circ - 2\arcsin(0.200/0.300) = 6.38^\circ\)), so the pillar is already almost
fully cut through by the filaments. Any bore merges with them; an isolated
hole would need \(d_h < 0.033\). **"Hole-pillar" is not geometrically
realised at \(D_p = 0.60\)** — those cases are enlarged openings, not a
separate jet orifice. State that when interpreting p60 h15/h30 results.

No filament–filament joint sphere on Pillar (`bridge_radius_m = 0`): coplanar
filaments fully interpenetrate at the node and the pillar covers it.
`wall_spacer_hole` is listed only for h15/h30 (rule 3-3).

---

## Sweep curvature margin (sinusoidal)

A tube of radius \(r\) swept along a plane curve self-intersects when the
centerline curvature radius \(R\) drops below \(r\). For a sine centerline
\(y = a\sin(2\pi x/\lambda)\), at a crest \(y' = 0\) and

\[
\kappa = a\left(\frac{2\pi}{\lambda}\right)^2,
\qquad
R = \frac{1}{\kappa} = \frac{\lambda^2}{4\pi^2 a}.
\]

The curvature margin is that radius over the tube radius:

\[
m = \frac{R}{r} = \frac{\lambda^2}{4\pi^2 a r}.
\]

\(m = 1\) is the geometric self-intersection limit. The previous hard gate
was \(m \ge 1.2\); that factor has no derivation in the repo (commit
`01956e8` records only the error string). It is now a warning band, not a
floor. Do not replace the three-band rule with a quieter numeric cutoff
(e.g. 0.95): a future sub-unity geometry must be named in the acknowledged
set or the gate raises.

Three-band rule (`validate_curvature_margin` in
`src/ro/manifest_validation.py`):

| band | action |
|------|--------|
| \(m \ge 1.2\) | pass silently |
| \(1.0 \le m < 1.2\) | pass with a WARNING naming the `geo_id` and the value (reduced margin against sweep self-intersection) |
| \(m < 1.0\) | pass only for `geo_id`s in `_CURVATURE_MARGIN_ACKNOWLEDGED_GEO_IDS`, with a louder WARNING that the swept surface self-intersects and the CAD heals it; **raise** for any other `geo_id` |

### Amplitude limit at \(\lambda = W/2\)

Campaign \(W = 3.465\,\mathrm{mm}\), tube radius \(r = 0.400\,\mathrm{mm}\).
At the shortest wavelength \(\lambda = W/2\), \(m \ge 1.2\) requires

\[
a \le 0.15840\,\mathrm{mm} = W/21.9.
\]

So \(a = W/20\) (\(0.17325\,\mathrm{mm}\), \(m = 1.097\)) and
\(a = W/18\) (\(0.1925\,\mathrm{mm}\), \(m = 0.9874\)) both fall below the
nominal band. This is a physical constraint on the design matrix, not an
arbitrary choice.

### Acknowledged sub-unity case `S_a193_l1733`

\(R = 0.39496\,\mathrm{mm}\) versus \(r = 0.400\,\mathrm{mm}\),
\(m = 0.9874\) (code `curvature_margin=0.9874065974644699`). Mesh succeeded
fully: 4,977,088 cells, ortho_min 0.121754, AR_max 48.41, skew_max 0.602,
13 import zones, coordinates exact (\(x\) 0..34.65, \(y\) \(\pm\)1.7325,
\(z\) \(\pm\)0.3853 mm). Quality is inside campaign range —
`S_a193_l6930` is worse (ortho 0.1118 / skew 0.640).

Solid volume from `/mesh/check`: a144_l1733 \(17.90907\,\mathrm{mm}^3\),
a193_l1733 \(18.53702\,\mathrm{mm}^3\), difference \(+0.628\,\mathrm{mm}^3\).
Nominal prediction from the arc-length factor (elliptic integral 1.06538
\(\to\) 1.11268 over 24.255 mm active, trimmed section \(0.49854\,\mathrm{mm}^2\))
plus the bridge term is \(+0.589\,\mathrm{mm}^3\). Measured exceeds nominal
by 6.6%, so the CAD healing of the self-intersection is a surface crease at
29 extrema, **not** a volume removal. No material is missing. Porosity
0.80629 vs 0.79949.
