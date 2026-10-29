# CHAMELEON engine — solution design

Stage-one engine: `resize(source, target_canvas) -> design`. Deterministic, no
model, runs offline in the starter Docker image with no extra dependencies.

## 1. What the renderer actually does (and why it matters)

Read from `renderer/converter/`. These facts drive several decisions below.

| Fact | Consequence for the engine |
|---|---|
| Elements are painted in `z_index` order. | Keep `z_index` exactly; never rely on list order. |
| `fit: contain` pads the asset inside the box; `cover` centre-crops; `fill` stretches. | Box aspect is what the structural check sees. Keep every non-`cover` image box at its source aspect (target: < 0.5 % drift, limit is 5 %). Only the background (`cover`) may change aspect. |
| Text wraps greedily at **spaces** using real Solstice glyph widths; a word wider than the box overflows; nothing is clipped vertically. | Text fitting must use the same measurement (we can import `renderer.converter.wrap` and the font manager). A fit is valid only if `lines × line_height × font_size ≤ height` **and** the longest word ≤ width. |
| Wrapping keeps character indices, so `style_runs` survive untouched. | Never modify `content`; `style_runs` pass through verbatim. |
| Shapes are rasterised at element size; `corner_radius` is clamped to `min(w,h)/2`. | Scale `corner_radius` with the shape so pills stay pills. |
| A missing asset renders as a grey box with a red border and a label. | Can't be removed (ids are fixed) or hidden (opacity 0 reads as a dishonest JSON). Keep it small, low-priority, in the corner it came from. |
| Rotation is honoured about the centre. | Use rotated AABBs for collision and canvas clamping; preserve `rotation`. |

## 2. Invariants the output must always satisfy

Enforced by a final validation pass, regardless of layout strategy:

1. Same ids, same count, all other fields passed through unchanged.
2. Every element's (rotated) bounding box inside the canvas — including
   decorations that bled off the source canvas. The structural checker is
   literal; bleeding is not worth the risk.
3. Non-`cover` image boxes keep source aspect.
4. `content`, `font_family`, `font_weight`, `font_style`, `color`,
   `fill_color`, `asset_id`, `opacity`, `style_runs` unchanged.
5. Every text box is tall and wide enough for its wrapped text at the
   returned `font_size` (measured with the real fonts).
6. No overlap between two "solid" elements from different units
   (text, pills, logo, hero, panels). Overlap with the background and with
   decorations is allowed — that's the intentional-layering case.
7. Nothing is dropped or hidden. Low-priority elements shrink to a floor,
   never to zero.

## 3. Pipeline

```
source + target
  │
  ├─ 1. parse        → per-element features (area %, centre, aspect, text metrics)
  ├─ 2. unitise      → group_id units + implicit units (shape directly under a text)
  ├─ 3. infer roles  → background / overlay / logo / hero / headline / subhead /
  │                    body / legal / pill(cta|badge) / decoration / minor-image
  ├─ 4. pick family  → same-shape | tall | wide | ribbon   (from aspect change)
  ├─ 5. candidates   → 2–4 template instantiations (hero side, stack order, budget)
  ├─ 6. solve each   → slot assignment, size budgets, text fit, gap distribution
  ├─ 7. validate+fix → clamp, de-collide, re-fit text, aspect check
  ├─ 8. score        → pick best candidate by internal rubric
  └─ output
```

### 3.1 Role inference (no semantic labels)

Ids may be opaque in the hidden set, so roles come from geometry only.

- **background**: image or shape covering ≥ 90 % of the canvas at the lowest
  z, or any `fit: cover` image that covers the canvas. Several allowed
  (gradient + semi-transparent overlay rectangle → `overlay`).
- **decoration**: ungrouped shape that is semi-transparent (opacity < 0.5 or
  fill alpha < 0.5), or partly off-canvas, or large and low-z with no text
  sitting on it. Anchored to the corner/edge it came from.
- **logo**: `contain` image, small (≤ 8 % of canvas), wide (w/h ≥ 1.5), near
  the top or a corner. Fallback: the smallest `contain` image.
- **hero**: the largest non-background image (≥ 10 % of canvas).
- **minor image**: any other image (watermark, badge art). Lowest priority.
- **text roles** by `font_size` (weighted ×1.15 for bold), not by box area:
  largest = `headline`; next = `subhead`; short all-caps text inside a pill =
  `label`; small size (≤ 40 % of headline) with long content, low on the
  canvas = `legal`; the rest = `body`.
- **pill unit**: a `group_id` containing one shape + one text. The largest
  pill lowest on the canvas = `cta`; the others = `badge`. A rectangle with
  no group that a text box sits ≥ 80 % inside becomes an implicit panel
  unit.

### 3.2 Priority ladder (who shrinks first when space is tight)

`headline > cta > logo > hero > subhead > badges > body > legal > decoration > minor-image`

Sizes are budgeted top-down: each unit has a preferred size
(source size × uniform scale `s = min(W/w0, H/h0)`), a floor (readability,
§3.4), and shrinks only when higher-priority units can't fit otherwise.

### 3.3 Layout families (chosen from `Δ = ln(r_target / r_source)`)

| Family | When | Behaviour |
|---|---|---|
| **same-shape** | \|Δ\| < 0.3 (1:1 ↔ 4:5, 16:9 ↔ 3:2) | Uniform scale everything by `s`, keep reading order; distribute the leftover length in the long axis into the *gaps* between vertical bands, proportionally to the source gaps. "Stretch the spacing, not the furniture." Decorations/background re-anchored. |
| **tall** | r < 0.8 and Δ < −0.3 | Single column, reading order: logo → hero → headline → subhead → body → cta → badges/legal. Centre-aligned if the source was centred, else left. |
| **wide** | 1.3 ≤ r ≤ 5 and Δ > 0.3 | Two columns: hero on the side it leaned to in the source (default left), text column on the other (logo → headline → subhead → cta). Badges near the logo; legal along the bottom edge. Column split ~40/60 by hero aspect. |
| **ribbon** | r > 5 (728×90, 970×90, 1584×396-ish) | One row of flex slots: logo │ headline (single line) │ subhead/body (small) │ cta. Hero shrinks to row height and sits beside the logo. Badges and legal become a second micro-row only if height allows; otherwise they take the floor size inline. |
| **extreme tall** | r < 0.35 (160×600 skyscraper) | `tall` with the ribbon's flex logic turned vertical. |

Each family is a slot tree (rows/columns with gutters = 3 % of the short
canvas side). The solver fills slots with units, assigns widths/heights from
budgets, then fits text (§3.4) and runs the validator.

### 3.4 Text fitting

For a text box of width `w` and allowed height `h`:

1. Start from `font_size × s` (uniform scale).
2. Binary-search the largest size ≤ start such that the wrapped line count
   × `line_height` × size ≤ `h`, no word overflows `w`, and `max_lines` (if
   any) is respected. Measurement uses the renderer's own `char_widths` so
   wraps match the PNG.
3. Floors per role, relative to the short canvas side `m = min(W, H)`:
   headline ≥ max(18, 0.22·m clipped to 64) for ribbons / 3.5 %·m otherwise;
   subhead/body ≥ max(12, 60 % of headline floor); label ≥ 11; legal ≥ 8.
4. Hierarchy guard: after fitting, keep `subhead ≤ 0.6 × headline` and
   `legal ≤ 0.5 × subhead` so the visual order survives.
5. Return height = actual wrapped height (plus the source's vertical
   padding ratio) so collision checks are real, not nominal.

Pill units are scaled as one rigid unit (shape, radius, label font) so the
label stays centred; the label is then re-fitted inside the shape with 10 %
horizontal padding.

### 3.5 Validate-and-fix pass

Runs on every candidate: clamp to canvas → pairwise overlap test on solid
units (rotated AABBs) → resolve along the layout axis (push later unit down/
right, then shrink the lower-priority unit toward its floor, then re-fit its
text) → re-check image aspects → re-check text fits. Iterates to a fixed
point or 20 rounds; if a candidate still violates a hard invariant it is
discarded.

### 3.6 Candidate scoring (internal rubric, mirrors §2.5 of the brief)

Score = weighted sum of:

- collisions between solid units (hard veto), text over the hero box (heavy)
- readability: distance below font floors, words overflowing, text height
  used vs. available
- hierarchy fidelity: headline/subhead size ratio vs. source; relative
  reading order preserved; CTA still the most prominent pill
- intentionality: elements snap to a shared left/centre axis; gap variance
  between adjacent units (lower is better); used-area fraction near the
  source's (avoid both crowding and emptiness)
- faithfulness: each unit's normalised centre close to where it was, weighted
  by priority (the chameleon stays the same animal)

Highest score wins; ties go to the candidate with the larger headline.

## 4. Edge cases handled explicitly

- **Arbitrary element counts (Level 3)**: text units flow in priority order;
  anything beyond the first N that fit at preferred size take floor size,
  and the family switches to its "dense" variant (tighter gutters, two text
  columns in `wide`).
- **Missing asset**: treated as minor-image; placed in its source corner at
  floor size, never hidden.
- **Rotated elements** (ribbon labels): rotation kept; layout uses the
  rotated bounding box; groups rotate as one.
- **Semi-transparent / overlapping shapes**: classified as decoration or
  overlay; allowed to overlap anything.
- **Degenerate inputs**: zero-size boxes, empty text, `font_size` < 1,
  unknown `fit` — pass through with safe defaults, never crash. Any
  exception in the layout path falls back to the same-shape family, and if
  that fails, to a clamped uniform scale, so an output is always written.
- **Background with wildly different aspect** (`cover`): crops centre.
  Nothing we can do without regenerating pixels, and we don't.

## 5. Code layout (as built)

```
engine/
  resize.py      orchestrator: units → candidates → repair → score → assemble
  units.py       Unit model, role inference, priority ladder, font floors
  textfit.py     measurement with the renderer's fonts + size search
  layout.py      Ctx, sizing, column/row packers, the four families
  validate.py    repair (clamp + de-collide), final element fixes, check_design
  score.py       internal rubric (with a breakdown for debugging)
  quality.py     brand/readability floors, visible-pixel sizing, tiny-canvas contract
  assets.py      read-only measurement of each asset's visible (non-transparent) bounds
  geometry.py    rotated AABBs, overlap
tools/
  matrix.py      every input × 14 catalogue formats → output/matrix/*.png sheets
  fuzz.py        synthetic design generator (python -m tools.fuzz 200)
  check.py       pytest-free full check; run it in Docker too (see its docstring)
  compare.py     source vs output side by side for every format
tests/
  test_invariants.py   samples × catalogue, identity, determinism, fuzz
```

`engine/main.py` and the Dockerfile stay untouched; the Dockerfile already
copies all of `engine/`. No new dependencies — skia and Pillow arrive with
`cr-renderer`. `tools/` and `tests/` are dev-only (pytest is not in
requirements.txt).

Behaviours added during implementation that §3 did not spell out:

- Overlaps that already existed in the source are intentional and exempt from
  collision repair and scoring (Level 3's legal text under the watermark).
- Source boxes for text are measured, not nominal, before banding; otherwise
  padded boxes merge separate rows.
- A source row that no longer fits across wraps into several rows (a ribbon
  source going to a poster); a column that cannot fit at its floors splits into
  2–3 side-by-side columns before fonts are squeezed further.
- In the ribbon family, legal text becomes a full-width strip along the bottom.
- `max_lines` is ignored on purpose: the renderer ignores it too.
- Final pass holds every text at ≥ 4 px and grows the box instead, then
  re-clamps, so nothing is ever invisible.
- Brand/readability floors (`quality.py`): logo shortest side ≥ max(10 px,
  6 % of the short side, 22 % of the height on banners), hero ≥ 20 % of the
  short side, headline ≥ 10 px, other text ≥ 6 px. The scorer charges a floor
  breach like a collision, every shrink path stops at the floors, and the tests
  require every sample × catalogue cell to meet them.
- Text measures ~2 % wider on Linux (FreeType rounds glyph advances) than on
  macOS. Scoring runs on Linux, so `tools/check.py` is run in the container as
  well as locally; layouts must not sit on a knife edge between the two.
- Image floors are measured on *visible* pixels (`assets.py` reads each
  asset's alpha bounds once; the logo lockup is 60 % transparent padding) and
  never exceed what the source itself showed at the target's scale, so a
  designer's deliberately small logo stays small.
- Priority runs all the way down. When nothing fits, text gives up height
  least-important-first (to its floor, then to a 4 px hard minimum), pills and
  images follow, the headline last; in a ribbon, badges, hero and minor images
  surrender width to a starved headline before the logo or CTA do. A post-pass
  keeps every text at or below the headline's size.
- Two-tier contract: canvases of 60 000 px² and up must meet every floor;
  below that (320×50 with 17 elements) nothing may fall under 4 px and the
  headline must stay the largest text. `tests/` and `tools/check.py` enforce
  both tiers.
- Sparse layouts grow: when a column would leave 40 %+ of its region empty
  and nothing had to shrink, everything is scaled up (to 1.4×) while it still
  fits and breaks no floor. A 1080² post on a 1080×1920 story no longer looks
  like a thumbnail on a wall.
- Performance: text measurement is memoised, unit boxes are computed inline,
  collision repair works on boxes computed once per round, and shrinking one
  unit re-sizes only its own source row. 275 elements: ~4–7 s (was ~60 s).

## 6. Testing plan

1. **Catalogue matrix**: every sample × ~14 real formats (IG post 1080²,
   portrait 1080×1350, story 1080×1920, Pinterest 1000×1500, YouTube thumb
   1280×720, Twitter 1600×900, FB cover 820×312, LinkedIn banner 1584×396,
   leaderboard 728×90, large leaderboard 970×90, medium rect 300×250,
   skyscraper 160×600, A4 2480×3508, billboard 970×250). Rendered to a
   contact sheet per sample for eyeballing.
2. **Invariant tests** (pytest): §2 holds for every matrix cell.
3. **Fuzz**: synthetic designs with 3–30 elements, random groups, rotations,
   missing assets, 1-char and 300-char strings — must never crash or break
   an invariant.
4. **Docker run** with `--network none` before every milestone commit.

## 7. Milestones

| # | Deliverable | Unlocks |
|---|---|---|
| 1 | features + roles + validator + same-shape family + text fit | Level 1 looks designed; nothing off-canvas anywhere |
| 2 | tall + wide families, pill units, gap distribution | Level 2 (1600×400) correct |
| 3 | ribbon family, priority budgets, dense variants | Level 3 (728×90) legible |
| 4 | candidates + scorer, catalogue matrix, fuzz | "holds up everywhere" |
| 5 | README, Docker verification, timing | Submission-ready (done: README.md, tools/check.py in Docker) |

## 8. Explicitly out of scope for stage one

Dropping or hiding elements, rewriting copy, changing font family,
recolouring, any pixel operation on assets, and any model call. A local
vision critic could replace §3.6's scorer later; the deterministic path stays
as the fallback.
