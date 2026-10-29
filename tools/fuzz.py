"""Synthetic design generator for robustness testing.

Produces schema-valid designs with random element counts, groups, rotations,
very long / very short strings, translucent shapes and missing assets. Used by
tests/test_invariants.py and runnable on its own:

    python -m tools.fuzz 200       # run 200 seeds, report failures
"""

import random
import sys
from typing import Dict, List

WORDS = ("solstice", "golden", "hour", "botanical", "sparkling", "infusion", "chase", "the",
         "light", "limited", "batch", "summer", "line", "shop", "now", "crafted", "wild",
         "honey", "orange", "zest", "rosemary", "2026", "edition", "award", "winning", "—")
FONTS = ("Solstice Serif", "Solstice Sans")
CANVASES = [(1080, 1080), (1080, 1350), (1080, 1920), (1200, 628), (1600, 400), (728, 90),
            (300, 250), (160, 600), (2480, 3508), (1280, 720), (820, 312), (1000, 1500)]


def _text(rng: random.Random, n_words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n_words))


def make_design(seed: int) -> Dict:
    rng = random.Random(seed)
    W, H = rng.choice(CANVASES)
    els: List[Dict] = []
    z = 0
    if rng.random() < 0.9:
        els.append({"id": "bg", "type": "image", "x": 0, "y": 0, "width": W, "height": H,
                    "z_index": 0, "asset_id": rng.choice(["solstice_bg_gradient.png", "nope.png"]),
                    "fit": "cover"})
    n = rng.randint(2, 28)
    for i in range(n):
        z += 1
        kind = rng.choices(["text", "image", "shape", "pill"], weights=[5, 2, 2, 2])[0]
        w = rng.uniform(0.08, 0.9) * W
        h = rng.uniform(0.03, 0.5) * H
        x = rng.uniform(-0.1, 1.0) * W - w * rng.random() * 0.2
        y = rng.uniform(-0.1, 1.0) * H
        rot = rng.choice([0, 0, 0, 0, -12, 7, 45, 90])
        if kind == "text":
            n_words = rng.choice([1, 2, 3, 6, 12, 30, 60])
            els.append({"id": f"t{i}", "type": "text", "x": x, "y": y, "width": w, "height": h,
                        "z_index": z, "rotation": rot, "content": _text(rng, n_words),
                        "font_family": rng.choice(FONTS), "font_size": rng.choice([10, 14, 22, 36, 60, 96]),
                        "font_weight": rng.choice(["regular", "bold"]), "color": "rgba(255,255,255,1)",
                        "text_align": rng.choice(["left", "center", "right"]),
                        "line_height": rng.choice([1.0, 1.2, 1.4]), "letter_spacing": rng.choice([0, 0, 2]),
                        "max_lines": rng.choice([None, None, 2, 4])})
        elif kind == "image":
            s = rng.uniform(0.05, 0.6) * min(W, H)
            els.append({"id": f"i{i}", "type": "image", "x": x, "y": y, "width": s * rng.uniform(0.5, 3),
                        "height": s, "z_index": z, "rotation": rot,
                        "asset_id": rng.choice(["solstice_bottle.png", "solstice_logo_lockup.png", "missing.png"]),
                        "fit": rng.choice(["contain", "contain", "cover", "fill"])})
        elif kind == "shape":
            els.append({"id": f"s{i}", "type": "shape", "x": x, "y": y, "width": w, "height": h,
                        "z_index": z, "rotation": rot, "shape_type": rng.choice(["rectangle", "ellipse", "line"]),
                        "fill_color": f"rgba(255,255,255,{rng.choice([1, 0.15, 0.5])})",
                        "opacity": rng.choice([1, 1, 0.3]), "corner_radius": rng.choice([0, 20, 200])})
        else:
            gid = f"g{i}"
            pw, ph = rng.uniform(0.1, 0.5) * W, rng.uniform(0.03, 0.12) * H
            els.append({"id": f"pb{i}", "type": "shape", "x": x, "y": y, "width": pw, "height": ph,
                        "z_index": z, "rotation": rot, "shape_type": "rectangle",
                        "fill_color": "rgba(232,106,58,1)", "corner_radius": ph / 2, "group_id": gid})
            els.append({"id": f"pl{i}", "type": "text", "x": x, "y": y + ph * 0.25, "width": pw, "height": ph * 0.5,
                        "z_index": z + 1, "rotation": rot, "content": _text(rng, rng.choice([1, 2, 4])).upper(),
                        "font_family": "Solstice Sans", "font_size": max(8, int(ph * 0.4)), "font_weight": "bold",
                        "color": "rgba(255,255,255,1)", "text_align": "center", "group_id": gid})
            z += 1
    if rng.random() < 0.2:  # degenerate extras
        els.append({"id": "zero", "type": "shape", "x": 10, "y": 10, "width": 0, "height": 0,
                    "z_index": 99, "shape_type": "rectangle", "fill_color": "rgba(0,0,0,1)"})
        els.append({"id": "empty", "type": "text", "x": 10, "y": 10, "width": 100, "height": 20,
                    "z_index": 99, "content": "", "font_family": "Solstice Sans", "font_size": 12,
                    "color": "rgba(255,255,255,1)"})
    return {"canvas": {"width": W, "height": H}, "elements": els}


def random_target(seed: int) -> Dict:
    rng = random.Random(seed * 7919)
    W, H = rng.choice(CANVASES)
    return {"width": W, "height": H}


if __name__ == "__main__":
    from engine.resize import _resize
    from engine.validate import check_design

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    fails = 0
    for seed in range(n):
        d = make_design(seed)
        t = random_target(seed)
        try:
            out = _resize(d, t["width"], t["height"])
            probs = check_design(d, out)
        except Exception as e:  # noqa: BLE001
            probs = [f"EXCEPTION {type(e).__name__}: {e}"]
        if probs:
            fails += 1
            print(f"seed {seed} ({d['canvas']['width']}x{d['canvas']['height']} -> {t['width']}x{t['height']}, "
                  f"{len(d['elements'])} els): {probs[:4]}")
    print(f"{fails}/{n} seeds with problems")
    raise SystemExit(1 if fails else 0)
