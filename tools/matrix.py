"""Run every design in input/ against a catalogue of real formats and tile the
renders into one contact sheet per design (output/matrix/<design>.png).

    python -m tools.matrix            # all samples
    python -m tools.matrix level2     # only files whose name contains 'level2'
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

from engine.resize import resize
from engine.validate import check_design
from renderer.converter.convert import render_design_to_png

CATALOGUE = [
    ("IG Post", 1080, 1080), ("IG Portrait", 1080, 1350), ("IG Story", 1080, 1920),
    ("Pinterest", 1000, 1500), ("YouTube Thumb", 1280, 720), ("Twitter", 1600, 900),
    ("FB Cover", 820, 312), ("LinkedIn Banner", 1584, 396), ("Leaderboard", 728, 90),
    ("Large Leaderboard", 970, 90), ("Billboard", 970, 250), ("Medium Rect", 300, 250),
    ("Skyscraper", 160, 600), ("A4 Poster", 2480, 3508),
]
TILE = 360
OUT = Path("output/matrix")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    files = [p for p in sorted(Path("input").glob("*.json")) if flt in p.name]
    bad = 0
    for path in files:
        task = json.loads(path.read_text())
        source = task["source"]
        tiles = []
        for name, w, h in CATALOGUE:
            t0 = time.time()
            out = resize(source, {"width": w, "height": h})
            dt = time.time() - t0
            problems = check_design(source, out)
            png = OUT / f"{path.stem}__{w}x{h}.png"
            render_design_to_png(out, "brand_kit/assets", str(png))
            (OUT / f"{path.stem}__{w}x{h}.json").write_text(json.dumps(out, indent=1))
            status = "ok" if not problems else f"{len(problems)} problems"
            bad += bool(problems)
            print(f"{path.stem:40s} {name:18s} {w:5d}x{h:<5d} {dt:5.2f}s {status}")
            for pr in problems:
                print("      -", pr)
            im = Image.open(png).convert("RGB")
            scale = min(TILE / im.width, TILE / im.height)
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
            tile = Image.new("RGB", (TILE, TILE + 18), (235, 231, 221))
            tile.paste(im, ((TILE - im.width) // 2, 18 + (TILE - im.height) // 2))
            ImageDraw.Draw(tile).text((4, 2), f"{name} {w}x{h} {status}", fill=(40, 40, 40))
            tiles.append(tile)
        cols = 5
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (TILE + 6), rows * (TILE + 24)), (255, 255, 255))
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % cols) * (TILE + 6), (i // cols) * (TILE + 24)))
        sheet.save(OUT / f"{path.stem}.png")
    print(f"{bad} cell(s) with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
