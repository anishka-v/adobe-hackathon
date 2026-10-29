"""Internal rubric used to pick between candidate layouts.

Mirrors the brief's §2.5 as far as geometry allows: collisions, readability,
hierarchy, intentional spacing, and faithfulness to where things were.
Higher is better. Hard failures return -inf.
"""

from __future__ import annotations

import math
from typing import List

from .geometry import area, overlap_area, union
from .layout import Ctx
from .quality import unit_issues
from .units import Unit, text_role_of


def _gap_variance(units: List[Unit], axis: int) -> float:
    boxes = sorted((u.box for u in units), key=lambda b: b[axis])
    gaps = []
    for a, b in zip(boxes, boxes[1:]):
        g = b[axis] - (a[axis] + a[axis + 2])
        if g > 0:
            gaps.append(g)
    if len(gaps) < 2:
        return 0.0
    mean = sum(gaps) / len(gaps)
    return math.sqrt(sum((g - mean) ** 2 for g in gaps) / len(gaps)) / max(mean, 1.0)


def score(units: List[Unit], ctx: Ctx, collisions: int, explain: dict | None = None) -> float:
    W, H = ctx.W, ctx.H
    solid = [u for u in units if u.solid]
    if not solid:
        return 0.0
    s = 100.0
    ex = explain if explain is not None else {}

    def take(name: str) -> None:
        ex[name] = round(s - ex.get("_run", 100.0), 2)
        ex["_run"] = s

    # collisions between solid units are the cardinal sin
    s -= 40.0 * collisions
    take("collisions")
    # an invisible logo or unreadable text is as bad as a collision
    s -= 40.0 * len(unit_issues(units, W, H))
    take("brand_floors")
    # text over the hero image box
    hero = next((u for u in solid if u.kind == "hero"), None)
    if hero is not None:
        hb = hero.box
        for u in solid:
            if u.is_text or u.is_pill:
                ov = overlap_area(hb, u.box) / max(1.0, area(u.box))
                s -= 25.0 * ov
    take("text_over_hero")

    # readability: text size relative to what the role wants on this canvas
    for u in solid:
        t = u.text_el
        if t is None:
            continue
        fs = int(t["font_size"])
        fl = ctx.floor(u)
        want = ctx.start_font(u, ctx.s_pref)
        if fs < fl:
            s -= 6.0 * (fl - fs) / fl * (u.priority / 100.0 + 0.5)
        else:
            s -= 4.0 * max(0.0, 1.0 - fs / max(want, 1.0)) * (u.priority / 100.0)
    take("readability")

    # hierarchy: headline stays the biggest text; subhead ratio close to source
    head = next((u for u in solid if u.kind == "headline"), None)
    if head is not None and head.text_el is not None:
        hf = int(head.text_el["font_size"])
        for u in solid:
            t = u.text_el
            if t is None or u is head:
                continue
            if int(t["font_size"]) > hf:
                s -= 8.0
            ratio_src = ctx.ratios.get(id(u))
            if ratio_src:
                ratio_now = int(t["font_size"]) / max(hf, 1)
                s -= 3.0 * min(1.0, abs(ratio_now - ratio_src) / max(ratio_src, 0.1))
    take("hierarchy")

    # intentional spacing / balance
    cb = union(u.box for u in solid)
    cx = (cb[0] + cb[2] / 2) / W - 0.5
    cy = (cb[1] + cb[3] / 2) / H - 0.5
    s -= 10.0 * (abs(cx) + abs(cy))
    s -= 3.0 * min(1.0, _gap_variance(solid, 1 if H >= W else 0))
    take("balance")

    # use of space: the design should fill a similar share of the canvas as before
    used_now = sum(area(u.box) for u in solid) / (W * H)
    used_src = sum(area(u.src_box) for u in solid) / (ctx.w0 * ctx.h0)
    s -= 12.0 * min(1.0, abs(used_now - used_src) / max(used_src, 0.05))
    # hero prominence
    if hero is not None:
        hero_now = area(hero.box) / (W * H)
        hero_src = area(hero.src_box) / (ctx.w0 * ctx.h0)
        s -= 8.0 * max(0.0, 1.0 - hero_now / max(hero_src, 1e-6)) * 0.5
    take("space_use")

    # faithfulness: priority-weighted displacement of normalised centres
    disp = 0.0
    wsum = 0.0
    for u in solid:
        b, sb = u.box, u.src_box
        dx = (b[0] + b[2] / 2) / W - (sb[0] + sb[2] / 2) / ctx.w0
        dy = (b[1] + b[3] / 2) / H - (sb[1] + sb[3] / 2) / ctx.h0
        disp += u.priority * math.hypot(dx, dy)
        wsum += u.priority
    # the further the shape moved, the more a relayout is expected
    delta = abs(math.log((W / H) / (ctx.w0 / ctx.h0)))
    s -= 15.0 / (1.0 + 3.0 * delta) * (disp / wsum if wsum else 0.0)
    take("faithfulness")

    # margins: nothing solid hugging the edge
    m = ctx.margin * 0.5
    for u in solid:
        b = u.box
        if b[0] < m or b[1] < m or b[0] + b[2] > W - m or b[1] + b[3] > H - m:
            s -= 1.0
    take("margins")
    ex.pop("_run", None)
    return s
