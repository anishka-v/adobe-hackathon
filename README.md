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

No model, no network, no dependencies beyond the starter kit's
`requirements.txt`. The three samples take 0.01–0.1 s each; a 275-element
design takes a few seconds.

## Approach

The engine never stretches a design. It takes the design apart into its
pieces, works out what each piece is for, and arranges those same pieces
again on the new canvas — the way a designer moves furniture into a room of a
different shape. Only positions, sizes and font sizes change.

**1. Group the pieces.** Some elements only make sense together: a button is
a pill shape plus its label, a ribbon is a rotated box plus its text. Elements
that share a `group_id`, or a text sitting on its own panel, are treated as
one piece from here on.

**2. Work out what each piece is.** The input carries no labels — only ids,
types and geometry — so roles come from shape and position:

| What the engine sees | What it concludes |
|---|---|
| an image covering the canvas | background |
| a small, wide image near the top edge | logo |
| the biggest remaining image | product |
| the largest text | headline; the next largest is the subhead |
| long, small text near the bottom | legal copy |
| the biggest button low on the canvas | call to action; other buttons are badges |
| a translucent or edge-bleeding shape | decoration |

Each role has a priority — headline, then call to action, logo, product,
subhead, badges, body text, legal, decoration — which decides what gives way
first when space is short.

**3. Choose a layout for the new shape.** Comparing the old and new aspect
ratios, the engine builds two to five candidate layouts:

- *nearly the same shape* — keep the design as it is, scale it, and put the
  extra room into the spacing between pieces;
- *taller* — one column in reading order, with the logo kept at the top;
- *wider* — product on one side, a column of text and buttons on the other;
- *a banner* (five or more times wider than tall) — a single row: logo,
  product, text, badges, button, with the legal copy as a thin strip along the
  bottom.

**4. Make everything fit.** Text is measured with the same fonts and
line-wrapping code the official renderer uses, so a text box that fits here
fits in the picture. When a layout is too crowded, the least important pieces
shrink first, and never below a readable size; then rows wrap, then a column
splits in two, and only as a last resort are fonts squeezed — the headline
last. When a layout would leave most of the canvas empty, everything grows a
little instead.

**5. Tidy up.** Every piece is kept inside the canvas, pieces that collide are
pushed apart (overlaps that were in the original are deliberate and left
alone), and every image keeps its proportions.

**6. Pick the best candidate.** Each candidate is scored on what the brief's
rubric asks for — no collisions, readable text, the brand intact, an even and
balanced arrangement, and faithfulness to the original — and the best one is
returned.

## How to test

**1. Run the samples and look.**

```bash
python -m engine.main
open output/
```

`engine/main.py` prints `OK` per task, or self-check warnings (missing fields,
off-canvas elements, mismatched ids). Open each `*_output.png` next to the
corresponding file in `input/` and check it against the rubric:

- nothing overlaps that shouldn't; text never sits on the product image;
- every word is readable at the image's real size, nothing is cut off;
- logo and bottle are intact and in proportion; fonts and colours unchanged;
- things line up, spacing is even, the headline is still the biggest thing.

**2. Run it the way scoring does.** Use the Docker commands above. You should
see the same three `OK` lines and real renders (brand fonts and artwork, not
labelled boxes — boxes would mean the renderer failed to import inside the
image). Do this step as well as the local one: text measures about 2 % wider
on Linux than on macOS, and scoring runs on Linux.

**3. Try other sizes or your own design.** Drop any task JSON (same shape as
the samples: `source` + `target_canvas`) into `input/` and re-run. Or resize
one design to any size directly:

```bash
python -c "
import json
from engine.resize import resize
from renderer.converter.convert import render_design_to_png
task = json.load(open('input/chameleon_sample_level2_core.json'))
out = resize(task['source'], {'width': 1080, 'height': 1920})
render_design_to_png(out, 'brand_kit/assets', 'output/try.png')
print('wrote output/try.png')
"
```

Formats worth trying, since hidden targets are real platform sizes: 1080×1080,
1080×1350, 1080×1920, 1280×720, 1600×900, 1584×396, 728×90, 970×90, 300×250,
160×600, 320×50, 2480×3508.

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
```

The input/output format is specified in `chameleon_design_schema_v0.9.json`.
