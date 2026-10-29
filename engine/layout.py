"""Layout families and the packing helpers they share.

Every family returns a list of Units positioned on the target canvas. The
solver works on *units* (see units.py): it sizes each one from a preferred
scale, shrinks the least important ones first when space runs out, and then
places them with even gaps. Backgrounds and decorations are anchored, not
packed.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import textfit
from .geometry import Box, union
from .quality import (HARD_MIN_PX, MIN_HEADLINE_PX, MIN_TEXT_PX, hard_shrink_factor, image_floor,
                      image_visible_dims, scale_within_floors, unit_ok)
from .units import Unit, floors_for, text_role_of


@dataclass
class Ctx:
    W: float
    H: float
    w0: float
    h0: float
    ribbon: bool = False
    dense: bool = False
    margin: float = 0.0
    gap: float = 0.0
    s_min: float = 1.0
    s_area: float = 1.0
    s_pref: float = 1.0
    ratios: Dict[int, float] = field(default_factory=dict)  # id(unit) -> src font / headline font
    headline_font: Optional[int] = None

    @staticmethod
    def make(W: float, H: float, w0: float, h0: float, units: Sequence[Unit], ribbon: bool = False, dense: bool = False) -> "Ctx":
        c = Ctx(W=W, H=H, w0=w0, h0=h0, ribbon=ribbon, dense=dense)
        short = min(W, H)
        c.margin = max(6.0, (0.035 if dense else 0.05) * short)
        c.gap = max(4.0, (0.4 if dense else 0.6) * c.margin)
        c.s_min = min(W / w0, H / h0)
        c.s_area = math.sqrt((W * H) / (w0 * h0))
        c.s_pref = c.s_area
        head = next((u for u in units if u.kind == "headline"), None)
        if head is not None and head.src_font:
            for u in units:
                if u.src_font:
                    c.ratios[id(u)] = u.src_font / head.src_font
        return c

    def floor(self, u: Unit) -> float:
        return floors_for(text_role_of(u), self.W, self.H, self.ribbon)

    def start_font(self, u: Unit, scale: float) -> float:
        """Preferred font size before fitting: source size at the preferred scale,
        bounded below by a readable target and above by the hierarchy guard."""
        role = text_role_of(u)
        base = (u.src_font or 12.0) * scale
        fl = self.floor(u)
        # a unit being shrunk (scale below s_pref) shrinks its readable target too;
        # otherwise the target pins the size and shrinking does nothing
        shrink = min(1.0, scale / self.s_pref) if self.s_pref > 0 else 1.0
        if self.ribbon:
            target = {"headline": 0.32 * self.H, "subhead": 0.16 * self.H,
                      "body": 0.14 * self.H, "legal": 0.10 * self.H, "label": 0.16 * self.H}.get(role, 0.14 * self.H)
            base = max(base, target * shrink)
        else:
            base = max(base, fl * 1.25 * shrink)
        if role != "headline" and self.headline_font:
            ratio = self.ratios.get(id(u), 0.5)
            base = min(base, self.headline_font * ratio * 1.1)
        return max(base, fl)


# ---------------------------------------------------------------------------
# Sizing one unit at a given scale (position is set to the origin; the packer
# moves it afterwards).
# ---------------------------------------------------------------------------

def _reset_origin(u: Unit) -> None:
    u.move_to(0.0, 0.0)


def size_text(u: Unit, width: float, max_h: Optional[float], ctx: Ctx, scale: float) -> None:
    t = u.text_el
    assert t is not None
    fl = ctx.floor(u)
    start = ctx.start_font(u, scale)
    width = max(width, 1.0)
    fs, m = textfit.fit_font_size(t, width, max_h, start, fl)
    if m.longest_word > width and width < ctx.W - 2 * ctx.margin:
        # widen to the longest word if the region allows (no mid-word breaks exist)
        width = min(ctx.W - 2 * ctx.margin, m.longest_word + 1)
        fs, m = textfit.fit_font_size(t, width, max_h, start, fl)
    t["font_size"] = int(fs)
    t["width"] = width
    t["height"] = m.height + 0.1 * fs
    t["x"], t["y"] = 0.0, 0.0
    if text_role_of(u) == "headline":
        ctx.headline_font = int(fs)


def size_pill(u: Unit, scale: float, max_w: float, ctx: Ctx) -> None:
    """Scale shape + label rigidly, then make sure the label fits on one line."""
    shp, lbl = u.shape_el, u.text_el
    assert shp is not None and lbl is not None
    u.scale(scale)
    _reset_origin(u)
    role_floor = floors_for("label", ctx.W, ctx.H, ctx.ribbon)
    fs = max(int(role_floor), int(lbl["font_size"]))
    if ctx.headline_font:
        fs = max(int(role_floor), min(fs, int(ctx.headline_font * 0.8)))
    pad = max(0.6 * fs, 0.08 * shp["width"])
    inner = shp["width"] - 2 * pad
    nat = textfit.natural_width(lbl, fs)
    while nat > inner and fs > role_floor:
        fs -= 1
        nat = textfit.natural_width(lbl, fs)
    if nat > inner:  # widen the pill to the label if the region allows
        want = nat + 2 * pad
        if want <= max_w:
            shp["width"] = want
        else:
            # the column is narrower than the label: shrink to legibility, no further
            # (a 4 px label that overhangs its pill beats a 1 px one that doesn't)
            shp["width"] = max_w
            while nat > shp["width"] - 2 * pad and fs > HARD_MIN_PX:
                fs -= 1
                nat = textfit.natural_width(lbl, fs)
    # keep pill height proportional to the label
    lh = float(lbl.get("line_height", 1.2) or 1.2)
    label_h = fs * lh
    shp["height"] = max(shp["height"], label_h * 1.3)
    if shp.get("corner_radius"):
        shp["corner_radius"] = min(float(shp["corner_radius"]), shp["height"] / 2, shp["width"] / 2)
    lbl["font_size"] = int(fs)
    lbl["width"] = shp["width"]
    lbl["height"] = label_h
    lbl["x"] = shp["x"]
    lbl["y"] = shp["y"] + (shp["height"] - label_h) / 2
    # other members of the group (rare) stay where the rigid scale put them
    bw = u.box[2]
    if bw > max_w and bw > 0:
        scale_within_floors(u, max_w / bw, ctx.W, ctx.H)
    _reset_origin(u)


def size_image(u: Unit, scale: float, max_w: float, max_h: float, ctx: Ctx) -> None:
    img = u.image_el
    assert img is not None
    # element dims, not the rotated bounding box, so the aspect survives rotation
    w, h = float(img["width"]) * scale, float(img["height"]) * scale
    if w <= 0 or h <= 0:
        w, h = max(w, 1.0), max(h, 1.0)
    s = min(1.0, max_w / w, max_h / h)
    img["width"], img["height"] = w * s, h * s
    img["x"], img["y"] = 0.0, 0.0


def size_generic(u: Unit, scale: float, max_w: float, max_h: float, ctx: Ctx, src: Dict[str, dict]) -> None:
    for e in u.elements:
        e.update(src[e["id"]])
    _reset_origin(u)
    u.scale(scale)
    u.scale_to_fit(max_w, max_h)
    _reset_origin(u)


def _lift_logo(u: Unit, region_w: float, region_h: float, ctx: Ctx) -> None:
    """A logo capped below its visible minimum is scaled back up, if the region allows."""
    _, _, w, h = u.box
    side = min(image_visible_dims(u))
    need = image_floor(u, ctx.W, ctx.H)
    if 0 < side < need and w > 0 and h > 0:
        f = min(need / side, region_w / w, region_h / h)
        if f > 1.0:
            u.scale(f)


class Sizer:
    """Re-sizes units from pristine copies so sizing is idempotent."""

    def __init__(self, units: Sequence[Unit], ctx: Ctx):
        self.ctx = ctx
        # shallow copies are enough: we only ever overwrite scalar fields
        self.pristine: Dict[int, Dict[str, dict]] = {
            id(u): {e["id"]: dict(e) for e in u.elements} for u in units
        }

    def restore(self, u: Unit) -> None:
        saved = self.pristine[id(u)]
        for e in u.elements:
            e.update(saved[e["id"]])

    def size(self, u: Unit, scale: float, region_w: float, region_h: float, shrink: float = 1.0,
             text_width: Optional[float] = None, hero_cap_h: Optional[float] = None) -> None:
        ctx = self.ctx
        self.restore(u)
        s = scale * shrink
        if u.is_text:
            w = text_width if text_width is not None else region_w
            size_text(u, min(w, region_w), None, ctx, s)
        elif u.is_pill:
            size_pill(u, s, region_w, ctx)
        elif u.is_image:
            cap_h = region_h
            if u.kind == "hero":
                cap_h = hero_cap_h if hero_cap_h is not None else region_h
            elif u.kind == "logo":
                cap_h = min(region_h, (0.8 * region_h) if ctx.ribbon else 0.12 * ctx.H)
                region_w = min(region_w, 0.5 * region_w if not ctx.ribbon else region_w)
            elif u.kind == "minor_image":
                cap_h = min(region_h, 0.6 * region_h if ctx.ribbon else 0.08 * ctx.H)
                region_w = min(region_w, 0.3 * region_w)
            size_image(u, s, region_w, cap_h, ctx)
            if u.kind == "logo":
                _lift_logo(u, region_w, region_h, ctx)
        else:
            size_generic(u, s, region_w, region_h, ctx, self.pristine[id(u)])


# ---------------------------------------------------------------------------
# Bands: units that sat side by side in the source stay side by side.
# ---------------------------------------------------------------------------

def bands_by_source_row(units: List[Unit]) -> List[List[Unit]]:
    ordered = sorted(units, key=lambda u: (u.src_box[1] + u.src_box[3] / 2, u.src_box[0]))
    bands: List[List[Unit]] = []
    for u in ordered:
        if bands:
            band = bands[-1]
            by0 = min(b.src_box[1] for b in band)
            by1 = max(b.src_box[1] + b.src_box[3] for b in band)
            uy0, uy1 = u.src_box[1], u.src_box[1] + u.src_box[3]
            ov = min(by1, uy1) - max(by0, uy0)
            if ov > 0.5 * min(u.src_box[3], by1 - by0):
                band.append(u)
                continue
        bands.append([u])
    for b in bands:
        b.sort(key=lambda u: u.src_box[0])
    return bands


def _text_width_for(u: Unit, region_w: float, ctx: Ctx) -> float:
    """Text boxes keep their relative width; wide source boxes become the column."""
    frac = u.src_box[2] / ctx.w0
    if frac >= 0.6:
        return region_w
    return max(min(region_w, region_w * frac / 0.85), min(region_w, 0.35 * region_w))


# ---------------------------------------------------------------------------
# Column packer
# ---------------------------------------------------------------------------

def pack_column(units: List[Unit], region: Box, ctx: Ctx, sizer: Sizer, align: Optional[str] = None,
                hero_cap: float = 0.45, spread: bool = True, allow_split: bool = True) -> float:
    """Stack `units` vertically inside `region`. Returns the total content height.

    When the stack does not fit: shrink the least important units first (never
    below a brand/readability floor), then tighten gaps, then split into 2-3
    side-by-side columns, then take height from text least-important first —
    the headline gives way last. When most of the region would be left empty,
    everything grows a little instead."""
    rx, ry, rw, rh = region
    if not units:
        return 0.0
    gap = ctx.gap
    shrink: Dict[int, float] = {id(u): 1.0 for u in units}
    grow = 1.0
    src_bands = bands_by_source_row(units)
    band_of = {id(u): b for b in src_bands for u in b}
    by_priority_desc = sorted(units, key=lambda u: -u.priority)  # headline first: hierarchy guard

    def size_unit(u: Unit) -> None:
        n_in_band = min(3, len(band_of[id(u)]))
        w_avail = rw if n_in_band == 1 else (rw - gap * (n_in_band - 1)) / n_in_band
        if u.is_pill or u.kind == "logo":
            # these only ever scale down to fit, so give them the whole column and
            # let the row wrap below, rather than shrinking labels to fit a slice
            w_avail = rw
        tw = _text_width_for(u, rw, ctx) if (u.is_text and n_in_band == 1) else w_avail
        sizer.size(u, ctx.s_pref * grow, w_avail, rh, shrink[id(u)], text_width=tw, hero_cap_h=hero_cap * rh)

    def wrap_rows() -> Tuple[List[List[Unit]], List[float], float]:
        """A source row that no longer fits across wraps into several rows
        (a modest shrink first, so pairs like "two badges" stay side by side)."""
        wrapped: List[List[Unit]] = []
        for band in src_bands:
            tot_w = sum(u.box[2] for u in band) + gap * (len(band) - 1)
            if tot_w > rw and len(band) > 1:
                f = (rw - gap * (len(band) - 1)) / max(1.0, tot_w - gap * (len(band) - 1))
                if f >= 0.7:
                    for u in band:
                        scale_within_floors(u, f, ctx.W, ctx.H)
                    if sum(u.box[2] for u in band) + gap * (len(band) - 1) <= rw + 0.5:
                        wrapped.append(band)
                        continue  # else a floor held a member up: wrap below instead
                row: List[Unit] = []
                row_w = 0.0
                for u in band:
                    uw = u.box[2]
                    if row and row_w + gap + uw > rw:
                        wrapped.append(row)
                        row, row_w = [], 0.0
                    row.append(u)
                    row_w += uw + (gap if len(row) > 1 else 0.0)
                if row:
                    wrapped.append(row)
            else:
                wrapped.append(band)
        heights = [max(u.box[3] for u in band) for band in wrapped]
        return wrapped, heights, sum(heights) + gap * (len(wrapped) - 1)

    def size_all(_u: Optional[Unit] = None) -> Tuple[List[List[Unit]], List[float], float]:
        for u in by_priority_desc:
            size_unit(u)
        return wrap_rows()

    def size_one(u: Unit) -> Tuple[List[List[Unit]], List[float], float]:
        # only this unit's source row needs re-sizing (the others are unchanged)
        for v in band_of[id(u)]:
            size_unit(v)
        return wrap_rows()

    bands, heights, total = size_all()

    # 1. shrink least-important first, stopping at the brand/readability floors
    for u in sorted(units, key=lambda u: u.priority):
        if total <= rh:
            break
        floor_scale = (0.6 if u.kind == "headline" else 0.3) if u.is_text else u.min_scale
        resize = size_all if u.kind == "headline" else size_one  # others depend on the headline's size
        while total > rh and shrink[id(u)] > floor_scale + 1e-6:
            prev = shrink[id(u)]
            shrink[id(u)] = max(floor_scale, prev * 0.85)
            bands, heights, total = resize(u)
            if not unit_ok(u, ctx.W, ctx.H):
                # that step broke a brand/readability floor: undo it, move on
                shrink[id(u)] = prev
                bands, heights, total = resize(u)
                break

    # 2. tighter gaps
    if total > rh:
        gap = max(2.0, gap * 0.4)
        bands, heights, total = wrap_rows()

    # 3. side-by-side columns
    if total > rh and allow_split and len(bands) >= 2 and rw >= 2.2 * ctx.margin * 4:
        k = min(3, max(2, int(math.ceil(total / rh))))
        k = min(k, len(bands))
        groups: List[List[Unit]] = [[] for _ in range(k)]
        acc, gi = 0.0, 0
        per = total / k
        for band, h in zip(bands, heights):
            if acc >= per * (gi + 1) - 1e-6 and gi < k - 1:
                gi += 1
            groups[gi].extend(band)
            acc += h + gap
        groups = [g for g in groups if g]
        k = len(groups)
        col_w = (rw - gap * (k - 1)) / k
        hs = []
        for i, g in enumerate(groups):
            # side-by-side columns share a top edge (each centred on its own looks accidental)
            hs.append(pack_column(g, (rx + i * (col_w + gap), ry, col_w, rh), ctx, sizer,
                                  align=align, hero_cap=hero_cap, spread=False, allow_split=False))
        return max(hs)

    # 4. last resort: non-text shrinks within its floors; then text gives up height
    #    least-important first - down to its floor, then (if it still doesn't fit)
    #    below the floor to the hard minimum, in the same order. Headline last.
    if total > rh and total > 0:
        f = max(0.3, (rh - gap * (len(bands) - 1)) / max(1.0, total - gap * (len(bands) - 1)))
        for u in units:
            if not u.is_text:
                scale_within_floors(u, f, ctx.W, ctx.H)
        bands, heights, total = wrap_rows()
        by_priority = sorted(units, key=lambda u: u.priority)
        for hard in (False, True):
            for u in by_priority:
                need = total - rh
                if need <= 0.5:
                    break
                if u.is_text:
                    t = u.text_el
                    lo = HARD_MIN_PX if hard else (MIN_HEADLINE_PX if u.kind == "headline" else MIN_TEXT_PX)
                    cur = int(t["font_size"])
                    if cur <= lo:
                        continue
                    fs, m = textfit.fit_font_size(t, t["width"], max(1.0, u.box[3] - need), cur, lo)
                    t["font_size"] = int(fs)
                    t["height"] = m.height + 0.1 * fs
                elif hard:
                    # pills and images only give way in the hard pass, and never below legibility
                    h = u.box[3]
                    f = hard_shrink_factor(u, max(0.25, (h - need) / h) if h > 0 else 1.0)
                    if f < 1.0:
                        u.scale(f)
                else:
                    continue
                bands, heights, total = wrap_rows()
            if total - rh <= 0.5:
                break

    # 5. sparse: grow everything a little rather than leave most of the region empty
    if spread and total < 0.6 * rh and all(v >= 1.0 - 1e-9 for v in shrink.values()):
        fails0 = sum(not unit_ok(u, ctx.W, ctx.H) for u in units)
        best = 1.0
        for g in (1.12, 1.25, 1.4):
            grow = g
            cand = size_all()
            if cand[2] > 0.85 * rh or sum(not unit_ok(u, ctx.W, ctx.H) for u in units) > fails0:
                break
            best = g
        if grow != best:
            grow = best
        bands, heights, total = size_all()

    # leftover: grow the hero a little, then spread gaps, then centre
    leftover = rh - total
    hero = next((u for u in units if u.kind == "hero"), None)
    if leftover > 0 and hero is not None and spread:
        hb = hero.box
        max_h = min(hero_cap * rh, hb[3] + leftover * 0.6)
        max_w = rw if len(next(b for b in bands if hero in b)) == 1 else hb[2] + leftover * 0.6
        f = min(max_h / hb[3], max_w / hb[2]) if hb[3] > 0 and hb[2] > 0 else 1.0
        if f > 1.0:
            hero.scale(f)
            bi = next(i for i, b in enumerate(bands) if hero in b)
            heights[bi] = max(u.box[3] for u in bands[bi])
            total = sum(heights) + gap * (len(bands) - 1)
            leftover = rh - total
    extra_gap = 0.0
    if leftover > 0 and len(bands) > 1 and spread:
        extra_gap = min(leftover / (len(bands) + 1), 2.0 * ctx.gap)
    content_h = total + extra_gap * (len(bands) - 1)
    y = ry + (rh - content_h) / 2 if spread else ry

    for band, h in zip(bands, heights):
        tot_w = sum(u.box[2] for u in band) + gap * (len(band) - 1)
        # a unit alone on its row keeps its source alignment, unless it was split out of a
        # shared source row (wrapped badges), where its old left/right slot means nothing
        lone_from_row = len(band) == 1 and len(band_of[id(band[0])]) > 1
        a = align or (band[0].align if len(band) == 1 and not lone_from_row else "center")
        if len(band) > 1:
            # a row that was spread across the source stays spread; a clustered one stays clustered
            src_span = max(u.src_box[0] + u.src_box[2] for u in band) - min(u.src_box[0] for u in band)
            spread_row = src_span > 0.6 * ctx.w0 and tot_w < rw
            row_gap = (rw - sum(u.box[2] for u in band)) / (len(band) - 1) if spread_row else gap
            row_gap = min(row_gap, 4 * gap)
            tot_w = sum(u.box[2] for u in band) + row_gap * (len(band) - 1)
        else:
            row_gap = gap
        if a == "left":
            x = rx
        elif a == "right":
            x = rx + rw - tot_w
        else:
            x = rx + (rw - tot_w) / 2
        for u in band:
            uw, uh = u.box[2], u.box[3]
            u.move_to(x, y + (h - uh) / 2)
            x += uw + row_gap
        y += h + gap + extra_gap
    return content_h


# ---------------------------------------------------------------------------
# Row packer (ribbons)
# ---------------------------------------------------------------------------

def _fit_within_floors(u: Unit, max_w: float, max_h: float, ctx: Ctx) -> None:
    """scale_to_fit, but never below a brand/readability floor the unit meets."""
    _, _, w, h = u.box
    if w > 0 and h > 0:
        f = min(max_w / w, max_h / h)
        if f < 1.0:
            scale_within_floors(u, f, ctx.W, ctx.H)


# How much more slowly the logo gives up width than other fixed columns in a ribbon
# (1.0 = same rate; smaller = protected). Tuned on the catalogue, see tools/matrix.py.
RIBBON_LOGO_SHRINK_EXP = 0.8


def pack_row(items: List[List[Unit]], region: Box, ctx: Ctx, sizer: Sizer) -> None:
    """`items` are columns (lists of units stacked vertically) laid out left to right.
    Each column's units are sized to the row height; text columns get the leftover width."""
    rx, ry, rw, rh = region
    gap = ctx.gap
    text_cols = [col for col in items if any(u.is_text for u in col)]
    fixed_cols = [col for col in items if col not in text_cols]
    shrink = 1.0

    def size_fixed() -> float:
        total = 0.0
        for col in fixed_cols:
            n = len(col)
            per_h = (rh - gap * 0.5 * (n - 1)) / n if n > 1 else rh
            for u in col:
                if u.kind == "hero":
                    cap_h = per_h
                    cap_w = 0.10 * rw
                elif u.kind == "logo":
                    cap_h = min(per_h, 0.8 * rh)
                    cap_w = 0.14 * rw
                elif u.kind in ("cta", "badge", "panel"):
                    cap_h = min(per_h, 0.6 * rh if n == 1 else per_h)
                    cap_w = 0.22 * rw
                else:
                    cap_h = min(per_h, 0.5 * rh)
                    cap_w = 0.08 * rw
                # priority ladder: the logo and CTA give up width far more slowly
                # than badges and the hero when the row runs out of room
                s_u = shrink ** (RIBBON_LOGO_SHRINK_EXP if u.kind == "logo" else 1.0)
                sizer.size(u, ctx.s_pref * s_u * 4, cap_w, cap_h)  # oversize then cap: fill the row height
                _fit_within_floors(u, cap_w, cap_h, ctx)
                if u.is_pill:
                    # pills: size from height so the label is readable
                    sizer.size(u, ctx.s_pref * s_u, cap_w, cap_h)
                    _fit_within_floors(u, cap_w, cap_h, ctx)
            total += max(u.box[2] for u in col)
        return total

    fixed_w = size_fixed()
    n_cols = len(items)
    avail = rw - fixed_w - gap * (n_cols - 1)
    need = (0.5 if text_cols else 0.0) * rw
    rounds = 0
    while avail < need and rounds < 10 and fixed_cols:
        shrink *= 0.85
        fixed_w = size_fixed()
        avail = rw - fixed_w - gap * (n_cols - 1)
        rounds += 1
    if avail < need and fixed_cols and fixed_w > 0:
        # floors held the pills up: squeeze the fixed columns uniformly as a last resort
        f = max(0.3, (rw - need - gap * (n_cols - 1)) / fixed_w)
        for col in fixed_cols:
            for u in col:
                scale_within_floors(u, f, ctx.W, ctx.H)
        fixed_w = sum(max(u.box[2] for u in col) for col in fixed_cols)
        avail = rw - fixed_w - gap * (n_cols - 1)
    # text columns share the leftover width, proportional to their max source width
    def pack_text_cols(avail_w: float) -> None:
        weights = [max(u.src_box[2] for u in col) for col in text_cols]
        tw_sum = sum(weights) or 1.0
        for col, wgt in zip(text_cols, weights):
            col_w = max(1.0, avail_w * wgt / tw_sum)
            # stack text inside the column; floors handled by pack_column. No column
            # split here: the row already budgeted exactly one column of this width.
            pack_column(col, (0.0, 0.0, col_w, rh), ctx, sizer, align="left", spread=False,
                        allow_split=False)
            # vertically centre the stack within the row
            cb = union(u.box for u in col)
            dy = (rh - cb[3]) / 2 - cb[1]
            for u in col:
                u.translate(0, dy)

    if text_cols:
        pack_text_cols(avail)
        # Priority: a headline below its floor outranks badges, the hero and minor images
        # sitting at theirs. Take width from those (logo and CTA last) and re-pack.
        head = next((u for col in text_cols for u in col if u.kind == "headline"), None)
        for _ in range(4):
            if head is None or int(head.text_el["font_size"]) >= MIN_HEADLINE_PX:
                break
            victims = [u for col in fixed_cols for u in col if u.kind not in ("logo", "cta")]
            if not victims:
                victims = [u for col in fixed_cols for u in col]
            changed = False
            for u in victims:
                f = hard_shrink_factor(u, 0.75)
                if f < 1.0:
                    u.scale(f)
                    changed = True
            if not changed:
                break
            fixed_w = sum(max(u.box[2] for u in col) for col in fixed_cols)
            avail = rw - fixed_w - gap * (n_cols - 1)
            pack_text_cols(avail)

    # place columns left to right; leftover spread into the gaps
    widths = [union(u.box for u in col)[2] for col in items]
    total = sum(widths) + gap * (n_cols - 1)
    leftover = rw - total
    extra = min(leftover / max(1, n_cols - 1), 3 * gap) if (leftover > 0 and n_cols > 1) else 0.0
    content_w = total + extra * (n_cols - 1)
    x = rx + (rw - content_w) / 2
    for col, w in zip(items, widths):
        n = len(col)
        if any(u.is_text for u in col):
            cb = union(u.box for u in col)
            for u in col:
                u.translate(x - cb[0], ry + (rh - cb[3]) / 2 - cb[1])
        else:
            col_h = sum(u.box[3] for u in col) + gap * 0.5 * (n - 1)
            y = ry + (rh - col_h) / 2
            for u in col:
                u.move_to(x + (w - u.box[2]) / 2, y)
                y += u.box[3] + gap * 0.5
        x += w + gap + extra


# ---------------------------------------------------------------------------
# Backgrounds and decorations
# ---------------------------------------------------------------------------

def place_background(u: Unit, ctx: Ctx) -> None:
    e = u.elements[0]
    if e["type"] == "image" and (e.get("fit") or "contain") != "cover":
        # aspect-locked full-bleed: contain within the canvas, centred
        sb = u.src_box
        asp = sb[2] / sb[3] if sb[3] else 1.0
        if ctx.W / ctx.H > asp:
            h, w = ctx.H, ctx.H * asp
        else:
            w, h = ctx.W, ctx.W / asp
        e.update({"x": (ctx.W - w) / 2, "y": (ctx.H - h) / 2, "width": w, "height": h})
    else:
        e.update({"x": 0.0, "y": 0.0, "width": ctx.W, "height": ctx.H})


def place_anchored(u: Unit, ctx: Ctx, scale: Optional[float] = None) -> None:
    """Keep a decoration at the same relative position, scaled, and inside the canvas."""
    sb = u.src_box
    cx = (sb[0] + sb[2] / 2) / ctx.w0
    cy = (sb[1] + sb[3] / 2) / ctx.h0
    s = scale if scale is not None else min(ctx.s_pref, ctx.s_min * 1.5)
    u.scale(s)
    u.scale_to_fit(0.6 * ctx.W, 0.9 * ctx.H)
    _, _, w, h = u.box
    x = cx * ctx.W - w / 2
    y = cy * ctx.H - h / 2
    x = min(max(0.0, x), ctx.W - w)
    y = min(max(0.0, y), ctx.H - h)
    u.move_to(x, y)


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------

def split_units(units: List[Unit]):
    bg = [u for u in units if u.kind == "background"]
    deco = [u for u in units if u.kind == "decoration"]
    solid = [u for u in units if u.solid]
    return bg, deco, solid


def family_same_shape(units: List[Unit], ctx: Ctx, variant: str = "gaps") -> None:
    """Uniform scale, then put the leftover length into gaps (or centre the block)."""
    bg, deco, solid = split_units(units)
    s = ctx.s_min
    for u in bg:
        place_background(u, ctx)
    for u in solid + deco:
        u.scale(s)
    sw, sh = ctx.w0 * s, ctx.h0 * s
    long_axis = 1 if ctx.H - sh > ctx.W - sw else 0
    leftover = (ctx.H - sh) if long_axis == 1 else (ctx.W - sw)
    off_x = (ctx.W - sw) / 2 if long_axis == 1 else 0.0
    off_y = (ctx.H - sh) / 2 if long_axis == 0 else 0.0

    if variant == "center" or not solid or leftover <= 0:
        for u in solid + deco:
            u.translate(off_x if long_axis == 1 else (ctx.W - sw) / 2,
                        off_y if long_axis == 0 else (ctx.H - sh) / 2)
        _fit_text_boxes(solid, ctx)
        _clamp_all(solid + deco, ctx)
        return

    # distribute leftover proportionally to the gaps between bands along the long axis
    idx0, idx2 = (1, 3) if long_axis == 1 else (0, 2)
    ordered = sorted(solid, key=lambda u: u.box[idx0])
    bands: List[List[Unit]] = []
    for u in ordered:
        b = u.box
        if bands:
            lo = min(v.box[idx0] for v in bands[-1])
            hi = max(v.box[idx0] + v.box[idx2] for v in bands[-1])
            if b[idx0] < hi - 1e-6:
                bands[-1].append(u)
                continue
        bands.append([u])
    edges = []
    prev = 0.0
    for band in bands:
        lo = min(v.box[idx0] for v in band)
        hi = max(v.box[idx0] + v.box[idx2] for v in band)
        edges.append((prev, lo))
        prev = hi
    edges.append((prev, sh if long_axis == 1 else sw))
    gaps = [max(0.0, b - a) for a, b in edges]
    gsum = sum(gaps)
    shifts = []
    acc = 0.0
    for g in gaps[:-1]:
        acc += leftover * (g / gsum if gsum > 0 else 1.0 / len(gaps))
        shifts.append(acc)
    for band, sh_ in zip(bands, shifts):
        for u in band:
            if long_axis == 1:
                u.translate(off_x, sh_)
            else:
                u.translate(sh_, off_y)
    for u in deco:
        place_anchored(u, ctx, scale=1.0)  # already scaled by s above
    _fit_text_boxes(solid, ctx)
    _clamp_all(solid + deco, ctx)


def _fit_text_boxes(solid: List[Unit], ctx: Ctx) -> None:
    """After a rigid scale, re-check each text box: if the wrapped text no longer
    fits (int rounding, narrower box), shrink the font until it does."""
    for u in solid:
        for t in u.elements:
            if t["type"] != "text":
                continue
            fs = int(t["font_size"])
            ok, m = textfit.fits(t, fs, t["width"], None)
            fl = ctx.floor(u) if u.is_text else floors_for("label", ctx.W, ctx.H, ctx.ribbon)
            if u.is_text:
                # keep the box tight to the wrapped height (text draws top-aligned,
                # so trimming the bottom never moves the glyphs)
                fs2, m2 = textfit.fit_font_size(t, t["width"], None, fs, min(fl, fs))
                t["font_size"] = int(fs2)
                t["height"] = m2.height + 0.1 * fs2
            elif not ok:
                while fs > 1 and not ok:
                    fs -= 1
                    ok, m = textfit.fits(t, fs, t["width"], None)
                t["font_size"] = int(fs)


def _clamp_all(units: List[Unit], ctx: Ctx) -> None:
    for u in units:
        u.scale_to_fit(ctx.W, ctx.H)
        x, y, w, h = u.box
        dx = min(0.0, ctx.W - (x + w)) - min(0.0, x)
        dy = min(0.0, ctx.H - (y + h)) - min(0.0, y)
        if dx or dy:
            u.translate(dx, dy)


def family_tall(units: List[Unit], ctx: Ctx, variant: str = "order") -> None:
    bg, deco, solid = split_units(units)
    for u in bg:
        place_background(u, ctx)
    for u in deco:
        place_anchored(u, ctx)
    sizer = Sizer(solid, ctx)
    m = ctx.margin
    real_src = {id(u): u.src_box for u in solid}
    col = list(solid)
    header = _top_header(col, ctx)  # from real source positions, before any reordering
    if variant == "hero_first":
        # hero right after the header, everything else in source reading order
        # (units that shared a source row keep sharing one, e.g. a row of badges)
        body = [u for u in col if u not in header]
        hero = [u for u in body if u.kind == "hero"]
        rows = [hero] + bands_by_source_row([u for u in body if u.kind != "hero"])
        for i, row in enumerate(r for r in rows if r):
            for u in row:
                u.src_box = (u.src_box[0], i * 10.0, u.src_box[2], 5.0)  # force band order
    region = (m, m, ctx.W - 2 * m, ctx.H - 2 * m)
    if header:
        # a logo the source anchored to the top edge stays there (with whatever
        # shared its row); the rest of the design is centred in the space below
        hh = pack_column(header, (m, m, ctx.W - 2 * m, 0.2 * (ctx.H - 2 * m)), ctx, sizer,
                         spread=False, allow_split=False)
        top = m + hh + 2 * ctx.gap
        col = [u for u in col if u not in header]
        region = (m, top, ctx.W - 2 * m, ctx.H - m - top)
    if col:
        pack_column(col, region, ctx, sizer, hero_cap=0.45)
    for u in solid:  # the scorer measures faithfulness against the real source
        u.src_box = real_src[id(u)]
    _clamp_all(solid, ctx)


def _top_header(units: List[Unit], ctx: Ctx) -> List[Unit]:
    """The logo plus anything in its source row, if the source pinned it to the top."""
    logo = next((u for u in units if u.kind == "logo"), None)
    if logo is None or len(units) < 3:
        return []
    if (logo.src_box[1] + logo.src_box[3] / 2) / ctx.h0 >= 0.2:
        return []
    row = next(b for b in bands_by_source_row(units) if logo in b)
    return [u for u in row if not u.is_text or u.kind == "legal"] if len(row) > 1 else [logo]


def family_wide(units: List[Unit], ctx: Ctx, variant: str = "hero_left") -> None:
    bg, deco, solid = split_units(units)
    for u in bg:
        place_background(u, ctx)
    for u in deco:
        place_anchored(u, ctx)
    sizer = Sizer(solid, ctx)
    m, g = ctx.margin, ctx.gap
    hero = next((u for u in solid if u.kind == "hero"), None)
    rest = [u for u in solid if u is not hero]
    region_x, region_w = m, ctx.W - 2 * m
    if hero is not None:
        hero_h = ctx.H - 2 * m
        sizer.size(hero, 10.0, 0.42 * region_w, hero_h, hero_cap_h=hero_h)  # fill the height, capped by width
        hw = hero.box[2]
        if variant == "hero_left":
            hero.move_to(m, m + (hero_h - hero.box[3]) / 2)
            region_x = m + hw + g * 2
        else:
            hero.move_to(ctx.W - m - hw, m + (hero_h - hero.box[3]) / 2)
        region_w = ctx.W - 2 * m - hw - g * 2
    head = next((u for u in rest if u.kind == "headline"), None)
    align = (head.text_el.get("text_align", "center") if head and head.text_el else "center") or "center"
    if hero is None:
        align = None
    pack_column(rest, (region_x, m, region_w, ctx.H - 2 * m), ctx, sizer, align=align, hero_cap=0.5)
    _clamp_all(solid, ctx)


def family_ribbon(units: List[Unit], ctx: Ctx, variant: str = "logo_left") -> None:
    bg, deco, solid = split_units(units)
    for u in bg:
        place_background(u, ctx)
    for u in deco:
        place_anchored(u, ctx)
    sizer = Sizer(solid, ctx)
    m = ctx.margin
    region = (m, m, ctx.W - 2 * m, ctx.H - 2 * m)
    by_kind: Dict[str, List[Unit]] = {}
    for u in solid:
        by_kind.setdefault(u.kind, []).append(u)
    legal = [u for u in solid if u.kind == "legal"]
    texts = [u for u in solid if u.is_text and u.kind != "legal"]
    texts.sort(key=lambda u: -u.priority)
    pills = [u for u in solid if u.is_pill and u.kind != "cta"]
    strip_h = 0.0
    if legal:
        for u in legal:
            fl = ctx.floor(u)
            size_text(u, ctx.W - 2 * m, None, ctx, 0.0)
            t = u.text_el
            t["font_size"] = int(fl)
            mm = textfit.measure(t, int(fl), t["width"])
            t["height"] = mm.height
            u.move_to(m, ctx.H - m * 0.5 - mm.height)
            strip_h = max(strip_h, mm.height + m * 0.5)
        region = (m, m * 0.6, ctx.W - 2 * m, ctx.H - m * 0.6 - strip_h - m * 0.3)
    cta = by_kind.get("cta", [])
    logo = by_kind.get("logo", [])
    hero = by_kind.get("hero", [])
    others = [u for u in solid if u not in texts and u not in pills and u not in cta
              and u not in logo and u not in hero and u not in legal]
    cols: List[List[Unit]] = []
    if variant == "logo_left":
        cols += [logo] if logo else []
        cols += [hero] if hero else []
    else:
        cols += [hero] if hero else []
        cols += [logo] if logo else []
    if texts:
        cols.append(texts)
    # badges: stack if the row is tall enough for two readable rows, else inline
    if pills:
        max_stack = max(1, min(3, int((ctx.H - 2 * m) // max(14.0, 0.26 * ctx.H))))
        while pills:
            cols.append(pills[:max_stack])
            pills = pills[max_stack:]
    for u in others:
        cols.append([u])
    if cta:
        cols.append(cta)
    cols = [c for c in cols if c]
    pack_row(cols, region, ctx, sizer)
    _clamp_all(solid, ctx)


def enforce_hierarchy(units: List[Unit]) -> None:
    """No text may end up larger than the headline (the source's largest text).
    Boxes keep their width; heights follow the re-measured text."""
    head = next((u for u in units if u.kind == "headline"), None)
    if head is None or head.text_el is None:
        return
    hf = int(head.text_el["font_size"])
    for u in units:
        if u is head:
            continue
        for t in u.elements:
            if t["type"] != "text" or int(t["font_size"]) <= hf:
                continue
            t["font_size"] = hf
            m = textfit.measure(t, hf, max(1.0, t["width"]))
            if u.is_pill:
                # keep the label centred in its pill
                shp = u.shape_el
                t["height"] = hf * float(t.get("line_height", 1.2) or 1.2)
                if shp is not None:
                    t["y"] = shp["y"] + (shp["height"] - t["height"]) / 2
            else:
                t["height"] = m.height + 0.1 * hf


FAMILIES = {
    "same_shape": family_same_shape,
    "tall": family_tall,
    "wide": family_wide,
    "ribbon": family_ribbon,
}
