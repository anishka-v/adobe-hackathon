"""Axis-aligned box helpers, including rotated bounding boxes.

All boxes are (x, y, w, h) tuples in canvas pixels, matching the schema.
"""

import math
from typing import Iterable, Tuple

Box = Tuple[float, float, float, float]


def rotated_aabb(x: float, y: float, w: float, h: float, rotation: float) -> Box:
    """Axis-aligned bounding box of a rectangle rotated about its centre."""
    if not rotation:
        return (x, y, w, h)
    theta = math.radians(rotation)
    c, s = abs(math.cos(theta)), abs(math.sin(theta))
    bw = w * c + h * s
    bh = w * s + h * c
    cx, cy = x + w / 2, y + h / 2
    return (cx - bw / 2, cy - bh / 2, bw, bh)


def element_aabb(el: dict) -> Box:
    return rotated_aabb(el["x"], el["y"], el["width"], el["height"], el.get("rotation", 0) or 0)


def union(boxes: Iterable[Box]) -> Box:
    boxes = list(boxes)
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def overlap_area(a: Box, b: Box) -> float:
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy


def area(b: Box) -> float:
    return max(0.0, b[2]) * max(0.0, b[3])


def contains_fraction(inner: Box, outer: Box) -> float:
    """Fraction of `inner`'s area that lies inside `outer`."""
    a = area(inner)
    return overlap_area(inner, outer) / a if a > 0 else 0.0
