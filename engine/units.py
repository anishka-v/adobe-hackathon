"""Units and role inference.

A *unit* is the thing the layout moves: a single element, or a group of
elements that travel together (a button's pill + label, a rotated ribbon,
a text sitting on its own panel). Roles are inferred purely from geometry
and type — the hidden test set gives no semantic ids.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

from .assets import asset_info
from .geometry import Box, area, contains_fraction, element_aabb, overlap_area, rotated_aabb, union
from . import textfit

# Higher = more important = shrinks last and is placed first when space is tight.
PRIORITY = {
    "headline": 100,
    "cta": 90,
    "logo": 85,
    "hero": 80,
    "subhead": 70,
    "badge": 60,
    "body": 55,
    "panel": 55,
    "group": 50,
    "accent": 40,
    "legal": 30,
    "decoration": 20,
    "minor_image": 10,
    "background": 0,
}

SOLID_KINDS = {"headline", "cta", "logo", "hero", "subhead", "badge", "body",
               "panel", "group", "accent", "legal", "minor_image"}


def _rgba_alpha(color: Optional[str]) -> float:
    if not color:
        return 1.0
    try:
        inner = color[color.index("(") + 1: color.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        return float(parts[3]) if len(parts) > 3 else 1.0
    except Exception:
        return 1.0


class Unit:
    def __init__(self, kind: str, elements: List[dict], text_role: Optional[str] = None):
        self.kind = kind
        self.text_role = text_role
        self.elements = elements
        self.src_box: Box = union(element_aabb(e) for e in elements)
        self.src_font = None
        t = self.text_el
        if t is not None:
            self.src_font = float(t.get("font_size", 12))
        self.align = "center"  # source horizontal alignment, set by build_units
        self.min_scale = 0.2   # floor scale for images/pills relative to s_pref
        self.key = tuple(sorted(e["id"] for e in elements))
        self.intentional: set = set()  # keys of units this one may overlap
        self.asset = None              # AssetInfo for image units (visible-pixel bounds)
        self.src_canvas = (1.0, 1.0)   # source canvas size, for scale-relative floors

    # -- accessors ---------------------------------------------------------
    @property
    def priority(self) -> int:
        return PRIORITY.get(self.kind, 50)

    @property
    def solid(self) -> bool:
        return self.kind in SOLID_KINDS

    @property
    def box(self) -> Box:
        # hot path (millions of calls per layout): no generators, no helper calls
        # for the common single, unrotated element
        els = self.elements
        if len(els) == 1:
            e = els[0]
            r = e.get("rotation", 0) or 0
            if not r:
                return (e["x"], e["y"], e["width"], e["height"])
            return rotated_aabb(e["x"], e["y"], e["width"], e["height"], r)
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for e in els:
            r = e.get("rotation", 0) or 0
            if r:
                x, y, w, h = rotated_aabb(e["x"], e["y"], e["width"], e["height"], r)
            else:
                x, y, w, h = e["x"], e["y"], e["width"], e["height"]
            if x < x0:
                x0 = x
            if y < y0:
                y0 = y
            if x + w > x1:
                x1 = x + w
            if y + h > y1:
                y1 = y + h
        return (x0, y0, x1 - x0, y1 - y0)

    @property
    def text_el(self) -> Optional[dict]:
        texts = [e for e in self.elements if e["type"] == "text"]
        if not texts:
            return None
        return max(texts, key=lambda e: float(e.get("font_size", 0)))

    @property
    def shape_el(self) -> Optional[dict]:
        shapes = [e for e in self.elements if e["type"] == "shape"]
        return shapes[0] if shapes else None

    @property
    def image_el(self) -> Optional[dict]:
        imgs = [e for e in self.elements if e["type"] == "image"]
        return imgs[0] if imgs else None

    @property
    def is_text(self) -> bool:
        return self.kind in ("headline", "subhead", "body", "legal") and len(self.elements) == 1

    @property
    def is_pill(self) -> bool:
        return self.kind in ("cta", "badge", "panel") and self.shape_el is not None and self.text_el is not None

    @property
    def is_image(self) -> bool:
        return self.kind in ("logo", "hero", "minor_image") and len(self.elements) == 1

    @property
    def rotated(self) -> bool:
        return any(abs(float(e.get("rotation", 0) or 0)) > 0.01 for e in self.elements)

    @property
    def aspect(self) -> float:
        b = self.src_box
        return b[2] / b[3] if b[3] > 0 else 1.0

    # -- transforms --------------------------------------------------------
    def translate(self, dx: float, dy: float) -> None:
        for e in self.elements:
            e["x"] += dx
            e["y"] += dy

    def move_to(self, x: float, y: float) -> None:
        bx, by, _, _ = self.box
        self.translate(x - bx, y - by)

    def scale(self, s: float) -> None:
        """Uniform scale about the unit's current top-left corner."""
        bx, by, _, _ = self.box
        for e in self.elements:
            e["x"] = bx + (e["x"] - bx) * s
            e["y"] = by + (e["y"] - by) * s
            e["width"] *= s
            e["height"] *= s
            if e["type"] == "text":
                # +1e-6: floor(4.0 * 0.999999…) must be 4, not 3 (hard-minimum guards rely on it)
                e["font_size"] = max(1, int(math.floor(float(e["font_size"]) * s + 1e-6)))
                if e.get("letter_spacing"):
                    e["letter_spacing"] = float(e["letter_spacing"]) * s
            elif e["type"] == "shape":
                if e.get("corner_radius"):
                    e["corner_radius"] = float(e["corner_radius"]) * s
                if e.get("stroke_width"):
                    e["stroke_width"] = float(e["stroke_width"]) * s

    def scale_to_fit(self, max_w: float, max_h: float) -> None:
        _, _, w, h = self.box
        if w <= 0 or h <= 0:
            return
        s = min(max_w / w, max_h / h)
        if s < 1:
            self.scale(s)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        ids = ",".join(e["id"] for e in self.elements)
        return f"Unit({self.kind}:{ids})"


# ---------------------------------------------------------------------------
# Role inference
# ---------------------------------------------------------------------------

def build_units(source: dict) -> List[Unit]:
    cw, ch = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    canvas_box: Box = (0.0, 0.0, cw, ch)
    canvas_area = cw * ch
    elements = [copy.deepcopy(e) for e in source["elements"]]
    for e in elements:  # normalise numerics once
        for k in ("x", "y", "width", "height"):
            e[k] = float(e.get(k, 0) or 0)

    units: List[Unit] = []
    used: set = set()

    # 1. Full-bleed backgrounds / overlays: anything covering >= 90% of the canvas.
    for e in elements:
        cover = contains_fraction(canvas_box, element_aabb(e))
        if cover >= 0.9 and e["type"] in ("image", "shape"):
            units.append(Unit("background", [e]))
            used.add(e["id"])

    # 2. Explicit groups.
    groups: Dict[str, List[dict]] = {}
    for e in elements:
        gid = e.get("group_id")
        if gid and e["id"] not in used:
            groups.setdefault(gid, []).append(e)
    pills: List[Unit] = []
    for gid, members in groups.items():
        has_shape = any(m["type"] == "shape" for m in members)
        has_text = any(m["type"] == "text" for m in members)
        if has_shape and has_text:
            u = Unit("badge", members, text_role="label")  # cta decided below
            pills.append(u)
        else:
            u = Unit("group", members)
        units.append(u)
        used.update(m["id"] for m in members)

    # 3. Implicit panels: an opaque ungrouped shape with a text box >= 80% inside it.
    for shp in [e for e in elements if e["type"] == "shape" and e["id"] not in used]:
        if _rgba_alpha(shp.get("fill_color")) < 0.5 or float(shp.get("opacity", 1) or 1) < 0.5:
            continue
        sbox = element_aabb(shp)
        inside = [t for t in elements if t["type"] == "text" and t["id"] not in used
                  and contains_fraction(element_aabb(t), sbox) >= 0.8]
        if len(inside) == 1:
            u = Unit("panel", [shp, inside[0]], text_role="label")
            pills.append(u)
            units.append(u)
            used.update((shp["id"], inside[0]["id"]))

    # 4. Remaining shapes: decoration vs accent.
    for shp in [e for e in elements if e["type"] == "shape" and e["id"] not in used]:
        translucent = _rgba_alpha(shp.get("fill_color")) < 0.5 or float(shp.get("opacity", 1) or 1) < 0.5
        bleeds = contains_fraction(element_aabb(shp), canvas_box) < 0.95
        big = area(element_aabb(shp)) > 0.15 * canvas_area
        kind = "decoration" if (translucent or bleeds or big) else "accent"
        units.append(Unit(kind, [shp]))
        used.add(shp["id"])

    # 5. Images: logo / hero / minor.
    imgs = [e for e in elements if e["type"] == "image" and e["id"] not in used]
    logo = None
    scored = []
    for e in imgs:
        b = element_aabb(e)
        frac = area(b) / canvas_area
        asp = b[2] / b[3] if b[3] > 0 else 1
        cy = (b[1] + b[3] / 2) / ch
        cx = (b[0] + b[2] / 2) / cw
        if frac > 0.08 or asp < 1.5:
            continue
        pts = 0.0
        pts += 3.0 if cy < 0.25 else (1.0 if cy > 0.85 else 0.0)
        pts += 1.0 if abs(cx - 0.5) < 0.1 else 0.5
        pts += 1.0 if asp >= 2.0 else 0.0
        pts += 0.5 if (e.get("fit") or "contain") == "contain" else 0.0
        scored.append((pts, -frac, e))
    if scored:
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        if scored[0][0] >= 2.0:
            logo = scored[0][2]
    if logo is None and len(imgs) >= 2:
        cands = [e for e in imgs if (element_aabb(e)[2] / max(1, element_aabb(e)[3])) >= 1.3]
        if cands:
            logo = min(cands, key=lambda e: area(element_aabb(e)))
    if logo is not None:
        units.append(Unit("logo", [logo]))
        used.add(logo["id"])
        imgs = [e for e in imgs if e["id"] != logo["id"]]
    if imgs:
        hero = max(imgs, key=lambda e: area(element_aabb(e)))
        if area(element_aabb(hero)) / canvas_area >= 0.04:
            units.append(Unit("hero", [hero]))
            used.add(hero["id"])
    for e in imgs:
        if e["id"] not in used:
            units.append(Unit("minor_image", [e]))
            used.add(e["id"])

    # 6. Free text: headline / subhead / body / legal by font weight.
    texts = [e for e in elements if e["type"] == "text" and e["id"] not in used]

    def weight(e: dict) -> float:
        w = float(e.get("font_size", 12))
        return w * (1.15 if (e.get("font_weight") or "").lower() == "bold" else 1.0)

    texts.sort(key=weight, reverse=True)
    if texts:
        head = texts[0]
        hw = weight(head)
        units.append(Unit("headline", [head], text_role="headline"))
        used.add(head["id"])
        subhead_taken = False
        for e in texts[1:]:
            w = weight(e)
            b = element_aabb(e)
            cy = (b[1] + b[3] / 2) / ch
            content = str(e.get("content", ""))
            fs = float(e.get("font_size", 12))
            small = fs <= max(13.0, 0.014 * ch) or w <= 0.3 * hw
            is_legal = len(content) >= 40 and small and cy > 0.75
            if is_legal:
                units.append(Unit("legal", [e], text_role="legal"))
            elif not subhead_taken and w >= 0.3 * hw:
                units.append(Unit("subhead", [e], text_role="subhead"))
                subhead_taken = True
            else:
                units.append(Unit("body", [e], text_role="body"))
            used.add(e["id"])

    # 7. Anything left (unknown types) travels as a generic solid unit.
    for e in elements:
        if e["id"] not in used:
            units.append(Unit("group", [e]))
            used.add(e["id"])

    # 8. CTA: the largest un-rotated pill in the lower half; else the largest pill.
    if pills:
        lower = [p for p in pills if (p.src_box[1] + p.src_box[3] / 2) / ch >= 0.5 and not p.rotated]
        pool = lower or [p for p in pills if not p.rotated] or pills
        cta = max(pool, key=lambda p: area(p.src_box))
        cta.kind = "cta"
        for p in pills:
            if p is not cta and p.kind == "panel":
                pass  # panels keep their kind (text-on-shape, not a button)

    # 9. Tight source boxes for text: the nominal box often has padding below a
    #    single line, which would merge separate rows into one band.
    for u in units:
        t = u.text_el
        if u.is_text and t is not None:
            m = textfit.measure(t, int(t.get("font_size", 12)), t["width"])
            x, y, w, h = u.src_box
            u.src_box = (x, y, w, min(h, m.height))

    # 10. Source alignment for each unit.
    for u in units:
        bx, _, bw, _ = u.src_box
        cx = (bx + bw / 2) / cw
        if abs(cx - 0.5) <= 0.08:
            u.align = "center"
        elif cx < 0.5:
            u.align = "left"
        else:
            u.align = "right"
        t = u.text_el
        if u.is_text and t is not None and bw >= 0.6 * cw:
            u.align = t.get("text_align", "center") or "center"

    # 11. Pairs of solid units that already overlapped in the source are a designer's
    #     choice (layered graphics); they are exempt from collision repair/scoring.
    solid = [u for u in units if u.solid]
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            ov = overlap_area(a.src_box, b.src_box)
            if ov > 0.02 * min(area(a.src_box), area(b.src_box)):
                a.intentional.add(b.key)
                b.intentional.add(a.key)

    # 12. Asset measurements (read-only) so size floors apply to visible pixels.
    for u in units:
        u.src_canvas = (cw, ch)
        img = u.image_el
        if img is not None:
            u.asset = asset_info(img.get("asset_id"))
    return units


def source_font_scale(units: List[Unit]) -> Dict[str, float]:
    """Font size of each text unit relative to the headline, for the hierarchy guard."""
    head = next((u for u in units if u.kind == "headline"), None)
    if head is None or not head.src_font:
        return {}
    out = {}
    for u in units:
        if u.src_font:
            out[id(u)] = u.src_font / head.src_font
    return out


def floors_for(kind_or_role: str, W: float, H: float, ribbon: bool) -> float:
    """Minimum readable font size for a role on a W×H canvas."""
    short = min(W, H)
    if ribbon:
        base = max(11.0, 0.14 * H)
        return {
            "headline": base,
            "subhead": max(7.0, 0.08 * H),
            "body": max(7.0, 0.08 * H),
            "label": max(7.0, 0.08 * H),
            "legal": max(6.0, 0.06 * H),
        }.get(kind_or_role, max(7.0, 0.08 * H))
    base = max(12.0, 0.022 * short)
    return {
        "headline": base,
        "subhead": max(9.0, 0.6 * base),
        "body": max(9.0, 0.55 * base),
        "label": max(8.0, 0.5 * base),
        "legal": max(7.0, 0.008 * short),
    }.get(kind_or_role, max(9.0, 0.55 * base))


def text_role_of(u: Unit) -> str:
    if u.text_role:
        return u.text_role
    return u.kind if u.kind in ("headline", "subhead", "body", "legal") else "body"


def natural_text_width(u: Unit, font_size: int) -> float:
    t = u.text_el
    return textfit.natural_width(t, font_size) if t else 0.0
