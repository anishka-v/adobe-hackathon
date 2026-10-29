"""Hard invariants: clamp to canvas, keep image aspect, no solid-on-solid collisions.

`repair()` edits units in place and returns the number of unresolved
collisions; `check_design()` audits a finished output dict against its source.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .geometry import Box, area, element_aabb, overlap_area
from . import textfit
from .quality import scale_within_floors
from .units import Unit


def clamp_unit(u: Unit, W: float, H: float) -> None:
    u.scale_to_fit(W, H)
    x, y, w, h = u.box
    dx = (-x if x < 0 else 0.0) + (W - (x + w) if x + w > W else 0.0)
    dy = (-y if y < 0 else 0.0) + (H - (y + h) if y + h > H else 0.0)
    if dx or dy:
        u.translate(dx, dy)


def _collision(a: Unit, b: Unit) -> float:
    """Overlap as a fraction of the smaller unit's area."""
    ab, bb = a.box, b.box
    ov = overlap_area(ab, bb)
    if ov <= 0:
        return 0.0
    return ov / max(1.0, min(area(ab), area(bb)))


def repair(units: List[Unit], W: float, H: float, axis: str = "y", tol: float = 0.02) -> int:
    solid = [u for u in units if u.solid]
    for u in units:
        clamp_unit(u, W, H)
    shrunk: Dict[int, float] = {}
    for _ in range(40):
        worst: Tuple[float, Unit, Unit] | None = None
        for i, a in enumerate(solid):
            for b in solid[i + 1:]:
                if b.key in a.intentional:
                    continue
                c = _collision(a, b)
                if c > tol and (worst is None or c > worst[0]):
                    worst = (c, a, b)
        if worst is None:
            return 0
        _, a, b = worst
        mover, anchor = (a, b) if a.priority <= b.priority else (b, a)
        ab, mb = anchor.box, mover.box
        # push along the chosen axis, away from the anchor's centre
        if axis == "y":
            if mb[1] + mb[3] / 2 >= ab[1] + ab[3] / 2:
                dy = (ab[1] + ab[3]) - mb[1] + 2
            else:
                dy = ab[1] - (mb[1] + mb[3]) - 2
            mover.translate(0, dy)
        else:
            if mb[0] + mb[2] / 2 >= ab[0] + ab[2] / 2:
                dx = (ab[0] + ab[2]) - mb[0] + 2
            else:
                dx = ab[0] - (mb[0] + mb[2]) - 2
            mover.translate(dx, 0)
        x, y, w, h = mover.box
        if x < 0 or y < 0 or x + w > W or y + h > H:
            # no room that way: shrink the mover (never below half size) and slide it back inside
            if shrunk.get(id(mover), 1.0) <= 0.5:
                break
            shrunk[id(mover)] = shrunk.get(id(mover), 1.0) * 0.85
            scale_within_floors(mover, 0.85, W, H)
            clamp_unit(mover, W, H)
            if _collision(mover, anchor) > tol:
                # try the other axis once
                mb = mover.box
                if axis == "y":
                    dx = (ab[0] + ab[2]) - mb[0] + 2 if mb[0] + mb[2] / 2 >= ab[0] + ab[2] / 2 else ab[0] - (mb[0] + mb[2]) - 2
                    mover.translate(dx, 0)
                else:
                    dy = (ab[1] + ab[3]) - mb[1] + 2 if mb[1] + mb[3] / 2 >= ab[1] + ab[3] / 2 else ab[1] - (mb[1] + mb[3]) - 2
                    mover.translate(0, dy)
                clamp_unit(mover, W, H)
    # count what's left
    left = 0
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            if b.key not in a.intentional and _collision(a, b) > tol:
                left += 1
    return left


def fix_image_aspects(out_elements: List[dict], src_by_id: Dict[str, dict], W: float, H: float) -> None:
    for e in out_elements:
        if e["type"] != "image" or (e.get("fit") or "contain") == "cover":
            continue
        s = src_by_id[e["id"]]
        if s["height"] <= 0 or s["width"] <= 0:
            continue
        asp = s["width"] / s["height"]
        cur = e["width"] / e["height"] if e["height"] > 0 else asp
        if abs(cur / asp - 1) > 0.002:
            # keep the larger dimension, derive the other, then clamp
            if cur > asp:
                e["width"] = e["height"] * asp
            else:
                e["height"] = e["width"] / asp
        # full-bleed cover images cover the canvas; others must sit inside it
        if e["width"] > W or e["height"] > H:
            f = min(W / e["width"], H / e["height"])
            e["width"] *= f
            e["height"] *= f
        e["x"] = min(max(0.0, e["x"]), max(0.0, W - e["width"]))
        e["y"] = min(max(0.0, e["y"]), max(0.0, H - e["height"]))


def clamp_elements(out_elements: List[dict], W: float, H: float) -> None:
    for e in out_elements:
        x, y, w, h = element_aabb(e)
        if w > W or h > H:
            f = min(W / w, H / h)
            cx, cy = e["x"] + e["width"] / 2, e["y"] + e["height"] / 2
            e["width"] *= f
            e["height"] *= f
            e["x"], e["y"] = cx - e["width"] / 2, cy - e["height"] / 2
            if e["type"] == "text":
                e["font_size"] = max(1, int(float(e["font_size"]) * f))
            x, y, w, h = element_aabb(e)
        dx = (-x if x < 0 else 0.0) + (W - (x + w) if x + w > W else 0.0)
        dy = (-y if y < 0 else 0.0) + (H - (y + h) if y + h > H else 0.0)
        e["x"] += dx
        e["y"] += dy


def check_design(source: dict, out: dict) -> List[str]:
    """Audit an output against the structural rules. Returns problem strings."""
    problems: List[str] = []
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    src = {e["id"]: e for e in source["elements"]}
    ids = [e["id"] for e in out["elements"]]
    if sorted(ids) != sorted(src) or len(ids) != len(set(ids)):
        problems.append("element ids differ from source")
    for e in out["elements"]:
        s = src.get(e["id"])
        if s is None:
            continue
        x, y, w, h = element_aabb(e)
        if x < -0.5 or y < -0.5 or x + w > W + 0.5 or y + h > H + 0.5:
            problems.append(f"{e['id']}: off-canvas")
        if e["type"] == "image" and (e.get("fit") or "contain") != "cover" and s["height"] > 0 and e["height"] > 0:
            asp_s, asp_o = s["width"] / s["height"], e["width"] / e["height"]
            if abs(asp_o / asp_s - 1) > 0.05:
                problems.append(f"{e['id']}: image aspect drifted {asp_o / asp_s:.3f}x")
        for k in ("content", "font_family", "asset_id", "fill_color", "color", "opacity", "shape_type"):
            if k in s and s[k] != e.get(k):
                problems.append(f"{e['id']}: '{k}' changed")
        if e["type"] == "text":
            if int(e["font_size"]) < 1:
                problems.append(f"{e['id']}: font_size < 1")
            ok, m = textfit.fits(e, int(e["font_size"]), e["width"], e["height"])
            if not ok:
                problems.append(f"{e['id']}: text does not fit its box "
                                f"(lines={m.lines}, h={m.height:.0f}/{e['height']:.0f}, "
                                f"word={m.longest_word:.0f}/{e['width']:.0f})")
    return problems


MIN_FONT = 4


def fix_text_boxes(out_elements: List[dict], W: float, H: float) -> None:
    """Make every text box tall/wide enough for its wrapped text at its int font
    size; if the canvas has no room, shrink the font instead."""
    for e in out_elements:
        if e["type"] != "text":
            continue
        fs = max(1, int(e["font_size"]))
        ok, m = textfit.fits(e, fs, e["width"], e["height"])
        if ok:
            continue
        rotated = abs(float(e.get("rotation", 0) or 0)) > 0.01
        if (not rotated and m.height > e["height"] and e["y"] + m.height <= H + 0.5
                and m.longest_word <= e["width"] + 0.5):
            e["height"] = m.height
            continue
        while fs > MIN_FONT and not ok:
            fs -= 1
            ok, m = textfit.fits(e, fs, e["width"], e["height"])
        if not ok:
            # nothing readable fits: hold the font at the floor and grow the box
            # (the caller re-clamps to the canvas afterwards)
            fs = max(fs, MIN_FONT)
            m = textfit.measure(e, fs, max(e["width"], 1.0))
            if m.longest_word > e["width"]:
                e["width"] = min(W, m.longest_word + 1)
                m = textfit.measure(e, fs, e["width"])
            e["height"] = max(e["height"], m.height)
        e["font_size"] = fs
