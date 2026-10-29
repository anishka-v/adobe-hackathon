"""Brand and readability floors.

Structural checks (validate.check_design) can pass on a layout that is still
useless: an 8×2 px logo or a 3 px label is on-canvas, in proportion and fits
its box. These floors catch that. Image floors apply to the *visible* pixels
(the brand kit's logo is 60 % transparent padding), and never ask for more
than the source design itself showed at the target's scale.

The scorer charges breaches by priority and severity, every shrink path stops
at the floors, and the tests require the sample designs never to break them.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

from .assets import visible_size
from .units import Unit

# Absolute minimum font sizes in px, after the renderer's int cast.
MIN_TEXT_PX = 6
MIN_HEADLINE_PX = 10
HARD_MIN_PX = 4  # nothing is legible below this; the final squeeze never goes under it


def logo_min_side(W: float, H: float) -> float:
    """Shortest visible side a logo may have and still read as a logo. On banner
    shapes the row height is the whole canvas, so the floor is a share of it."""
    side = max(10.0, 0.06 * min(W, H))
    if W / H >= 4.0:
        side = max(side, 0.20 * H)
    return side


def hero_min_side(W: float, H: float) -> float:
    return max(16.0, 0.12 * min(W, H))


def _src_visible_short_side(u: Unit) -> float:
    e = u.image_el
    _, _, w, h = u.src_box
    if e is None:
        return min(w, h)
    vw, vh = visible_size(u.asset, w, h, e.get("fit") or "contain")
    return min(vw, vh)


def image_floor(u: Unit, W: float, H: float) -> float:
    """Visible-side floor for a logo/hero on a W×H canvas: the role's minimum,
    capped at what the source showed scaled to the target (a designer's small
    logo stays small)."""
    need = logo_min_side(W, H) if u.kind == "logo" else hero_min_side(W, H)
    w0, h0 = u.src_canvas
    s_pref = math.sqrt((W * H) / max(1.0, w0 * h0))
    return min(need, max(4.0, _src_visible_short_side(u) * s_pref))


def image_visible_dims(u: Unit, el: dict | None = None) -> Tuple[float, float]:
    """Visible width/height of an image unit as drawn (from `el`, default its own element)."""
    e = el if el is not None else u.image_el
    if e is None:
        _, _, w, h = u.box
        return w, h
    return visible_size(u.asset, float(e["width"]), float(e["height"]), e.get("fit") or "contain")


def _breaches(u: Unit, W: float, H: float, elements: Sequence[dict]) -> List[Tuple[str, float]]:
    """(message, severity in (0, 1]) for every floor `u` breaks, judged on `elements`."""
    out: List[Tuple[str, float]] = []
    if u.kind in ("logo", "hero"):
        img = next((e for e in elements if e["type"] == "image"), None)
        if img is not None:
            vw, vh = image_visible_dims(u, img)
            side, need = min(vw, vh), image_floor(u, W, H)
            if side < need - 0.5:  # half-pixel tolerance: outputs are rounded to 0.01 px
                out.append((f"{u.kind} '{img['id']}' too small (visible {vw:.0f}x{vh:.0f}, need {need:.0f})",
                            (need - side) / need))
    for e in elements:
        if e["type"] != "text" or not str(e.get("content", "")).strip():
            continue
        fs = int(e["font_size"])
        floor = MIN_HEADLINE_PX if u.kind == "headline" else MIN_TEXT_PX
        if fs < floor:
            out.append((f"'{e['id']}' text {fs}px < {floor}px", (floor - fs) / floor))
    return out


def unit_issues(units: Iterable[Unit], W: float, H: float) -> List[str]:
    return [msg for u in units for msg, _ in _breaches(u, W, H, u.elements)]


def unit_penalty(units: Iterable[Unit], W: float, H: float) -> float:
    """Floor breaches weighted by the unit's priority and how far below the floor
    it is: a slightly-small badge label costs little, a crushed headline a lot."""
    total = 0.0
    for u in units:
        weight = 0.4 + 0.6 * u.priority / 100.0
        for _, sev in _breaches(u, W, H, u.elements):
            total += weight * min(1.0, 1.5 * sev)
    return total


def design_issues(units: Iterable[Unit], out: Dict) -> List[str]:
    """Same floors, checked against a finished output dict (roles from `units`)."""
    by_id = {e["id"]: e for e in out["elements"]}
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    issues: List[str] = []
    for u in units:
        els = [by_id[e["id"]] for e in u.elements if e["id"] in by_id]
        issues += [msg for msg, _ in _breaches(u, W, H, els)]
    return issues


def unit_ok(u: Unit, W: float, H: float) -> bool:
    return not _breaches(u, W, H, u.elements)


def scale_within_floors(u: Unit, f: float, W: float, H: float) -> float:
    """Shrink `u` by up to `f` without breaking a floor it currently meets.
    Returns the factor actually applied (1.0 when no shrink was possible)."""
    if f >= 1.0:
        u.scale(f)
        return f
    if not unit_ok(u, W, H):
        # already below a floor: still never below legibility (4 px labels / 4 px visible art)
        g = hard_shrink_factor(u, f)
        u.scale(g)
        return g
    # restore from a snapshot, not by scaling back: Unit.scale floors font sizes to
    # whole px, so scale(g) then scale(1/g) loses size (7px -> 4px -> 6px)
    snapshot = [dict(e) for e in u.elements]
    for g in (f, f ** 0.5, f ** 0.25):
        u.scale(g)
        if unit_ok(u, W, H):
            return g
        for e, saved in zip(u.elements, snapshot):
            e.clear()
            e.update(saved)
    return 1.0


def hard_shrink_factor(u: Unit, f: float) -> float:
    """The factor (>= f) that shrinks `u` as far as `f` allows without pushing
    any label below HARD_MIN_PX or an image below a quarter of its size."""
    lo = f
    for e in u.elements:
        if e["type"] == "text":
            lo = max(lo, HARD_MIN_PX / max(1, int(e["font_size"])))
        elif e["type"] == "image":
            side = min(image_visible_dims(u, e))
            lo = max(lo, HARD_MIN_PX / side if side > 0 else 1.0)
    return min(1.0, lo)


def tiny_issues(units: Iterable[Unit], out: Dict) -> List[str]:
    """The contract for canvases too small for the floors (e.g. 17 elements on a
    320×50 mobile banner): nothing below legibility, and the headline stays the
    largest text so the hierarchy survives even when sizes don't."""
    by_id = {e["id"]: e for e in out["elements"]}
    issues: List[str] = []
    head_fs = None
    for u in units:
        if u.kind == "headline" and u.text_el is not None:
            head_fs = int(by_id[u.text_el["id"]]["font_size"])
    for u in units:
        if not u.solid:
            continue
        for e in u.elements:
            o = by_id[e["id"]]
            if o["type"] == "text" and str(o.get("content", "")).strip():
                fs = int(o["font_size"])
                if fs < HARD_MIN_PX:
                    issues.append(f"'{o['id']}' text {fs}px < {HARD_MIN_PX}px")
                if head_fs is not None and u.kind != "headline" and fs > head_fs:
                    issues.append(f"'{o['id']}' {fs}px is larger than the headline ({head_fs}px)")
            elif o["type"] == "image" and u.kind in ("logo", "hero"):
                side = min(image_visible_dims(u, o))
                if side < HARD_MIN_PX - 0.5:
                    issues.append(f"{u.kind} '{o['id']}' visible side {side:.0f}px < {HARD_MIN_PX}px")
    return issues
