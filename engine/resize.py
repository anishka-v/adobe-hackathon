"""
CHAMELEON engine — `resize()` is the entry point `engine/main.py` calls.

Approach (see DESIGN.md):
  1. parse the source into *units* (elements that travel together) and infer
     each unit's role from geometry alone (units.py);
  2. pick layout families from how much the aspect ratio changes and build
     2–5 candidate layouts (layout.py);
  3. repair each candidate against the hard invariants (validate.py);
  4. score every candidate with an internal copy of the rubric (score.py) and
     return the best.

Nothing here touches pixels: assets are only moved and resized, text keeps
its content and font family, and every element id is returned exactly once.
"""

from __future__ import annotations

import copy
import math
import sys
import traceback
from typing import Any, Dict, List, Tuple

from . import layout as L
from .score import score as score_layout
from .units import Unit, build_units
from .validate import clamp_elements, fix_image_aspects, fix_text_boxes, repair

NUMERIC = ("x", "y", "width", "height", "corner_radius", "stroke_width", "letter_spacing")


def _candidates(r_src: float, r_tgt: float) -> List[Tuple[str, str, bool]]:
    """(family, variant, dense) to try, from the aspect change Δ = ln(r_tgt / r_src)."""
    delta = math.log(r_tgt / r_src)
    cands: List[Tuple[str, str, bool]] = []
    if r_tgt >= 4.5:
        cands += [("ribbon", "logo_left", True), ("ribbon", "hero_left", True), ("wide", "hero_left", True)]
    elif abs(delta) < 0.3:
        cands += [("same_shape", "gaps", False), ("same_shape", "center", False)]
        cands += [("tall", "order", False)] if r_tgt < 1.0 else [("wide", "hero_left", False)]
    elif delta > 0:
        cands += [("wide", "hero_left", False), ("wide", "hero_right", False), ("same_shape", "gaps", False)]
        if r_tgt >= 2.5:
            cands.append(("ribbon", "logo_left", False))
    else:
        cands += [("tall", "order", False), ("tall", "hero_first", False), ("same_shape", "gaps", False)]
        if r_tgt < 0.4:
            cands.append(("tall", "order", True))
    # a plain column is a sane answer for any shape; let the scorer decide
    if ("tall", "order", False) not in cands:
        cands.append(("tall", "order", False))
    return cands


def _assemble(source: dict, units: List[Unit], W: int, H: int) -> Dict[str, Any]:
    by_id = {e["id"]: e for u in units for e in u.elements}
    src_by_id = {e["id"]: e for e in source["elements"]}
    out_elements = []
    for s in source["elements"]:
        e = dict(s)
        n = by_id.get(s["id"])
        if n is not None:
            for k in NUMERIC:
                if k in n:
                    e[k] = float(n[k])
            if e["type"] == "text":
                e["font_size"] = max(1, int(n.get("font_size", s.get("font_size", 12))))
        out_elements.append(e)
    fix_image_aspects(out_elements, src_by_id, W, H)
    clamp_elements(out_elements, W, H)
    fix_text_boxes(out_elements, W, H)
    clamp_elements(out_elements, W, H)
    for e in out_elements:
        for k in NUMERIC:
            if k in e and isinstance(e[k], float):
                e[k] = round(e[k], 2)
    # last: fonts only, judged on the rounded values the renderer will actually get
    fix_text_boxes(out_elements, W, H, grow=False)
    return {"canvas": {"width": W, "height": H}, "elements": out_elements}


def _fallback(source: dict, W: int, H: int) -> Dict[str, Any]:
    """Uniform contain-scale, centred — only used if the layout path raises."""
    w0, h0 = source["canvas"]["width"], source["canvas"]["height"]
    s = min(W / w0, H / h0)
    ox, oy = (W - w0 * s) / 2, (H - h0 * s) / 2
    out = []
    for e in source["elements"]:
        e = dict(e)
        full = e["width"] >= 0.9 * w0 and e["height"] >= 0.9 * h0
        if full:
            e.update({"x": 0.0, "y": 0.0, "width": float(W), "height": float(H)})
        else:
            e["x"], e["y"] = e["x"] * s + ox, e["y"] * s + oy
            e["width"], e["height"] = e["width"] * s, e["height"] * s
        if e["type"] == "text":
            e["font_size"] = max(1, int(e["font_size"] * s))
        out.append(e)
    design = {"canvas": {"width": W, "height": H}, "elements": out}
    clamp_elements(design["elements"], W, H)
    return design


def resize(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    W, H = int(target_canvas["width"]), int(target_canvas["height"])
    try:
        return _resize(source, W, H)
    except Exception:  # never fail to produce an output, but say so in the log
        print("[chameleon] layout failed, writing the uniform fallback:\n" + traceback.format_exc(),
              file=sys.stderr)
        return _fallback(source, W, H)


def _resize(source: dict, W: int, H: int) -> Dict[str, Any]:
    w0, h0 = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    base_units = build_units(source)
    r_src, r_tgt = w0 / h0, W / H
    best: Tuple[float, List[Unit]] | None = None
    for family, variant, dense in _candidates(r_src, r_tgt):
        units = copy.deepcopy(base_units)
        ctx = L.Ctx.make(W, H, w0, h0, units, ribbon=(family == "ribbon"), dense=dense)
        try:
            L.FAMILIES[family](units, ctx, variant)
            L.enforce_hierarchy(units)
            axis = "x" if family == "ribbon" else "y"
            collisions = repair(units, W, H, axis=axis)
            sc = score_layout(units, ctx, collisions)
        except Exception as e:
            print(f"[chameleon] candidate {family}/{variant} failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if best is None or sc > best[0]:
            best = (sc, units)
    if best is None:
        return _fallback(source, W, H)
    return _assemble(source, best[1], W, H)
