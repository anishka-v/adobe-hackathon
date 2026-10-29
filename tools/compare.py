"""Source-vs-output renders, side by side, for every input × catalogue format.

    python -m tools.compare            # all inputs
    python -m tools.compare level3     # only files whose name contains 'level3'

Writes output/compare/<design>__<W>x<H>.png (left: the source design, right:
the engine's output at that format). This is the fastest way to answer the
judge's question "is it unmistakably the same design?".
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from engine.resize import resize
from renderer.converter.convert import render_design_to_png
from tools.matrix import CATALOGUE

PANEL = 640
OUT = Path("output/compare")


def _fit(im: Image.Image, box: int) -> Image.Image:
    scale = min(box / im.width, box / im.height)
    return im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    for path in sorted(Path("input").glob("*.json")):
        if flt not in path.name:
            continue
        source = json.loads(path.read_text())["source"]
        sw, sh = source["canvas"]["width"], source["canvas"]["height"]
        src_png = OUT / f"{path.stem}__source.png"
        render_design_to_png(source, "brand_kit/assets", str(src_png))
        src_im = _fit(Image.open(src_png).convert("RGB"), PANEL)
        for name, w, h in CATALOGUE:
            out = resize(source, {"width": w, "height": h})
            png = OUT / f"{path.stem}__{w}x{h}.png"
            render_design_to_png(out, "brand_kit/assets", str(png))
            out_im = _fit(Image.open(png).convert("RGB"), PANEL)
            sheet = Image.new("RGB", (2 * PANEL + 30, PANEL + 28), (235, 231, 221))
            sheet.paste(src_im, (10, 28 + (PANEL - src_im.height) // 2))
            sheet.paste(out_im, (PANEL + 20, 28 + (PANEL - out_im.height) // 2))
            draw = ImageDraw.Draw(sheet)
            draw.text((10, 8), f"source  {sw}x{sh}", fill=(40, 40, 40))
            draw.text((PANEL + 20, 8), f"{name}  {w}x{h}", fill=(40, 40, 40))
            sheet.save(png)
            print(f"{path.stem:40s} {name:18s} -> {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
