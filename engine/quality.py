"""Brand and readability floors.

Structural checks (validate.check_design) can pass on a layout that is still
useless: an 8×2 px logo or a 3 px label is on-canvas, in proportion and fits
its box. These floors catch that. The scorer penalises any candidate that
breaks one, and the tests require the sample designs never to break them.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from .units import Unit

# Absolute minimum font sizes in px, after the renderer's int cast.
MIN_TEXT_PX = 6
MIN_HEADLINE_PX = 10


def logo_min_side(W: float, H: float) -> float:
    """Shortest side a logo may have and still read as a logo. On banner shapes
    the row height is the whole canvas, so the floor is a share of it."""
    side = max(10.0, 0.06 * min(W, H))
    if W / H >= 4.0:
        side = max(side, 0.22 * H)
    return side


def hero_min_side(W: float, H: float) -> float:
    return max(16.0, 0.2 * min(W, H))


def unit_issues(units: Iterable[Unit], W: float, H: float) -> List[str]:
    issues: List[str] = []
    for u in units:
        if u.kind == "logo":
            _, _, w, h = u.box
            if min(w, h) < logo_min_side(W, H):
                issues.append(f"logo too small ({w:.0f}x{h:.0f})")
        elif u.kind == "hero":
            _, _, w, h = u.box
            if min(w, h) < hero_min_side(W, H):
                issues.append(f"hero too small ({w:.0f}x{h:.0f})")
        for e in u.elements:
            if e["type"] != "text" or not str(e.get("content", "")).strip():
                continue
            fs = int(e["font_size"])
            floor = MIN_HEADLINE_PX if u.kind == "headline" else MIN_TEXT_PX
            if fs < floor:
                issues.append(f"{e['id']} text {fs}px < {floor}px")
    return issues


def design_issues(units: Iterable[Unit], out: Dict) -> List[str]:
    """Same floors, checked against a finished output dict (roles from `units`)."""
    by_id = {e["id"]: e for e in out["elements"]}
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    issues: List[str] = []
    for u in units:
        els = [by_id[e["id"]] for e in u.elements if e["id"] in by_id]
        if not els:
            continue
        if u.kind in ("logo", "hero"):
            e = els[0]
            side = min(e["width"], e["height"])
            need = logo_min_side(W, H) if u.kind == "logo" else hero_min_side(W, H)
            if side < need:
                issues.append(f"{u.kind} '{e['id']}' too small ({e['width']:.0f}x{e['height']:.0f})")
        for e in els:
            if e["type"] != "text" or not str(e.get("content", "")).strip():
                continue
            fs = int(e["font_size"])
            floor = MIN_HEADLINE_PX if u.kind == "headline" else MIN_TEXT_PX
            if fs < floor:
                issues.append(f"'{e['id']}' text {fs}px < {floor}px")
    return issues


def unit_ok(u: Unit, W: float, H: float) -> bool:
    return not unit_issues([u], W, H)


def scale_within_floors(u: Unit, f: float, W: float, H: float) -> float:
    """Shrink `u` by up to `f` without breaking a floor it currently meets.
    Returns the factor actually applied (1.0 when no shrink was possible)."""
    if f >= 1.0 or not unit_ok(u, W, H):
        u.scale(f)  # nothing to protect (already below a floor) or growing
        return f
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
