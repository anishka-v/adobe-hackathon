# CHAMELEON engine

A resize engine for the Adobe University Hackathon 2026 Chameleon Challenge.
Given a design and a target canvas, it returns the same elements re-laid-out
for the new shape: nothing stretched, nothing dropped, nothing redrawn.

```
input/<task>.json  ──►  engine/resize.py  ──►  output/<task>_output.json + .png
```

## Run it

```bash
pip install -r requirements.txt     # Python 3.10+; installs cr-renderer for previews
python -m engine.main               # every task in input/ -> output/
```

Exactly as stage-one scoring runs it:

```bash
docker build --platform linux/amd64 -t my-chameleon-engine .
docker run --rm --network none \
  -v "$(pwd)/input:/app/input" -v "$(pwd)/output:/app/output" \
  my-chameleon-engine
```

No model, no network, no extra dependencies beyond the starter kit's
`requirements.txt`. The three samples take 0.01–0.1 s each; a 275-element
design takes a few seconds.

## Approach

Deterministic relayout, in six steps (details in [DESIGN.md](DESIGN.md)):

1. **Units.** Elements that travel together become one unit: a `group_id`
   (a button's pill + label, a rotated ribbon), or a text sitting on its own
   panel.
2. **Roles from geometry alone.** No ids are trusted. Full-bleed → background;
   small, wide, top-anchored image → logo; largest image → hero; texts ranked
   by font size → headline / subhead / body / legal; largest low pill → CTA,
   other pills → badges; translucent or bleeding shapes → decoration.
3. **Layout families**, chosen by how far the aspect ratio moves:
   *same-shape* (uniform scale, leftover goes into the gaps), *tall* (one
   column; the logo stays pinned to the top), *wide* (hero on one side, text
   column on the other), *ribbon* (one row: logo │ hero │ text │ badges │ CTA,
   legal as a strip). Several candidates are built per task.
4. **Fitting.** Text is measured with the renderer's own fonts and wrap code,
   so what fits here fits in the PNG. When space runs out, the least important
   units shrink first — never below brand/readability floors — then rows wrap,
   columns split, and finally fonts are squeezed least-important-first. The
   headline gives way last. When most of a canvas would be empty, everything
   grows a little instead.
5. **Repair.** Clamp to canvas, resolve collisions between solid units
   (overlaps that existed in the source are intentional and left alone), keep
   every non-cover image at its source aspect.
6. **Score and pick.** Each candidate is scored against an internal copy of
   the rubric: collisions, floor breaches (weighted by priority), readability,
   hierarchy, balance, use of space, faithfulness to the source. Best wins.

What never changes: element ids and count, `content`, `font_family`, colours,
`asset_id`, `opacity`, `z_index`, `style_runs`, and the pixel content of every
asset. The engine *reads* the logo and product art once to find their visible
bounds (the logo lockup is 60 % transparent padding) so size floors apply to
what a viewer actually sees; it never writes, crops or regenerates them.

## Guarantees

For every output, checked by `tests/` on the samples × 24 real formats and on
200 random designs:

- same ids, nothing off-canvas (rotated bounds included), non-cover images
  within 0.5 % of their source aspect, protected fields untouched;
- every text box is wide and tall enough for its wrapped text at the returned
  integer font size;
- on canvases of 60 000 px² and up: the logo and hero stay visibly sized, the
  headline is ≥ 10 px, all other text ≥ 6 px;
- on smaller canvases (a 320×50 mobile banner with 17 elements cannot meet
  those): nothing below 4 px, and the headline stays the largest text;
- deterministic, and the public `resize()` never raises — if the layout path
  fails it logs the traceback and writes a clamped uniform scale instead.

## Verify

```bash
python -m pytest -q            # 268 tests, ~10 s
python -m tools.check          # same checks without pytest, runs inside the image:
docker run --rm --network none --entrypoint python \
  -v "$(pwd)/engine:/app/engine" -v "$(pwd)/tools:/app/tools" \
  -v "$(pwd)/input:/app/input" my-chameleon-engine -m tools.check
python -m tools.matrix         # contact sheet per design, 24 formats -> output/matrix/
python -m tools.compare        # source vs output side by side  -> output/compare/
python -m tools.fuzz 500       # random designs; any structural failure is printed
```

Run the Docker check as well as the local one: text measures ~2 % wider on
Linux (FreeType rounding) than on macOS, and scoring runs on Linux.

## Layout

```
engine/
  resize.py     entry point: units -> candidates -> repair -> score -> assemble
  units.py      unit model, role inference, priority ladder, font floors
  textfit.py    text measurement with the renderer's fonts (memoised)
  layout.py     the four families, column/row packers, growth, hierarchy
  validate.py   clamp, de-collide, aspect and text-box repair, check_design()
  quality.py    brand/readability floors, visible-pixel sizing, tiny contract
  score.py      internal rubric
  assets.py     read-only visible-bounds measurement of assets
  geometry.py   rotated bounding boxes
  main.py       starter orchestrator (unchanged)
tools/          matrix, compare, fuzz, check   (dev only, not in the image)
tests/          invariants over samples x formats, identity, determinism, fuzz
DESIGN.md       design notes and the decisions behind them
```

The starter kit's rules and FAQ are in [RULES.md](RULES.md) and
[FAQ.md](FAQ.md); the schema is `chameleon_design_schema_v0.9.json`.

## Known limits

- Extreme banners with long copy (Level 3 at 728×90) are dense: a 13–15 px
  three-line headline over 6–8 px badges is about what 90 px of height allows
  without dropping elements, which stage one forbids.
- Decorative shapes that bled off the source canvas are pulled inside it, since
  the structural check is literal about off-canvas elements.
- Nothing is rewritten, re-fonted or dropped. Those are finale features.
