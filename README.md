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

Deterministic relayout, in six steps:

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

Decisions worth knowing:

- **Priority ladder:** headline > CTA > logo > hero > subhead > badges > body >
  legal > decoration > minor image. It governs who shrinks first, who gives up
  width in a ribbon, and who is placed first.
- **Floors on visible pixels.** The brand logo is 60 % transparent padding, so
  the engine reads each asset's alpha bounds once (`engine/assets.py`) and
  sizes the logo and hero by what a viewer sees. Floors never exceed what the
  source itself showed at the target's scale, so a deliberately small logo
  stays small.
- **Two-tier contract.** On canvases of 60 000 px² and up every floor holds
  (logo and hero visibly sized, headline ≥ 10 px, other text ≥ 6 px). Below
  that — 17 elements on a 320×50 mobile banner — nothing drops under 4 px and
  the headline stays the largest text.
- **Source rows stay rows.** Elements that sat side by side keep their relative
  positions; a row that no longer fits wraps rather than shrinks.

What never changes: element ids and count, `content`, `font_family`, colours,
`asset_id`, `opacity`, `z_index`, `style_runs`, and the pixel content of every
asset. The engine only ever repositions and resizes.

## Guarantees

The engine enforces these on every output before returning it:

- same ids, nothing off-canvas (rotated bounds included), non-cover images
  within 0.5 % of their source aspect, protected fields untouched;
- every text box is wide and tall enough for its wrapped text at the returned
  integer font size, judged on the exact values the renderer receives;
- deterministic: the same input always yields the same output;
- `resize()` never raises — if the layout path fails it logs the traceback to
  stderr and writes a clamped uniform scale instead.

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

The starter kit's rules and FAQ are in [RULES.md](RULES.md) and
[FAQ.md](FAQ.md); the schema is `chameleon_design_schema_v0.9.json`.

## Known limits

- Extreme banners with long copy (Level 3 at 728×90) are dense: a 13–15 px
  three-line headline over 6–8 px badges is about what 90 px of height allows
  without dropping elements, which stage one forbids.
- Decorative shapes that bled off the source canvas are pulled inside it, since
  the structural check is literal about off-canvas elements.
- Nothing is rewritten, re-fonted or dropped. Those are finale features.
